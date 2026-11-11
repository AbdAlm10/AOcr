"""Hardware detection and engine choice.

Local Chandra is a ~5.3B vision model. The upstream launcher runs it in
bfloat16, which needs roughly 12 GB of weights plus room for an image and the
KV cache. 16 GB is the smallest card their own docs treat as comfortable.
CPU inference is not offered: a full page can take many minutes and still run
out of memory.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field

import httpx


@dataclass
class GpuInfo:
    name: str
    memory_mib: int
    driver: str = ""


@dataclass
class Probe:
    gpus: list[GpuInfo] = field(default_factory=list)
    vllm_reachable: bool = False
    hf_installed: bool = False
    datalab_configured: bool = False
    explicit: str = "auto"
    allow_cpu: bool = False


@dataclass
class Decision:
    engine: str | None
    ready: bool
    demo: bool
    title_en: str
    title_ar: str
    detail_en: str
    detail_ar: str
    speed_en: str
    speed_ar: str


def parse_nvidia_csv(text: str) -> list[GpuInfo]:
    gpus: list[GpuInfo] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("Failed") or line.startswith("NVIDIA-SMI"):
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            memory = int(float(parts[1]))
        except ValueError:
            continue
        driver = parts[2] if len(parts) > 2 else ""
        gpus.append(GpuInfo(name=parts[0], memory_mib=memory, driver=driver))
    return gpus


def query_nvidia_smi() -> list[GpuInfo]:
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0:
        return []
    return parse_nvidia_csv(completed.stdout)


def hf_stack_installed() -> bool:
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except Exception:
        return False
    return True


def vllm_reachable(api_base: str, api_key: str, timeout: float = 0.8) -> bool:
    url = api_base.rstrip("/") + "/models"
    try:
        response = httpx.get(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def best_vram(gpus: list[GpuInfo]) -> int:
    if not gpus:
        return 0
    return max(gpu.memory_mib for gpu in gpus)


def decide(probe: Probe) -> Decision:
    explicit = (probe.explicit or "auto").strip().lower()
    vram = best_vram(probe.gpus)
    gpu_name = probe.gpus[0].name if probe.gpus else ""

    if explicit == "mock":
        return Decision(
            engine="mock",
            ready=True,
            demo=True,
            title_en="Demo mode",
            title_ar="وضع التجربة",
            detail_en=(
                "The model is not running. Uploads still flow through the real "
                "queue, and the text you see is a sample so the desk can be tried "
                "without a GPU."
            ),
            detail_ar=(
                "النموذج غير شغّال. الرفع والطابور حقيقيان، والنص الظاهر عيّنة "
                "لتجربة المكتب من دون بطاقة رسومات."
            ),
            speed_en="Instant sample text. Not an OCR result.",
            speed_ar="نص فوري للتجربة. ليس نتيجة تعرّف.",
        )

    if explicit == "vllm":
        return _vllm_decision(probe.vllm_reachable, forced=True)
    if explicit == "hf":
        return _hf_decision(
            vram, gpu_name, probe.hf_installed, forced=True, allow_cpu=probe.allow_cpu
        )
    if explicit == "datalab":
        return _datalab_decision(probe.datalab_configured, forced=True)

    if explicit not in ("auto", ""):
        return _none(
            f"Unknown CHANDRA_ENGINE={explicit}. Use auto, vllm, hf, or datalab.",
            f"قيمة CHANDRA_ENGINE غير معروفة ({explicit}). استخدم auto أو vllm أو hf أو datalab.",
        )

    if probe.vllm_reachable:
        return _vllm_decision(True, forced=False)
    if vram >= 14000 and probe.hf_installed:
        return _hf_decision(vram, gpu_name, True, forced=False)
    if probe.datalab_configured:
        return _datalab_decision(True, forced=False, gpu_idle=vram >= 12000 and not probe.hf_installed)
    if vram >= 12000:
        return _none(
            f"{gpu_name or 'A GPU'} has {vram / 1024:.0f} GB. Start the vLLM server "
            "(best quality and speed) or install the Hugging Face extra and set "
            "CHANDRA_ENGINE=hf. A Datalab API key also works if you set DATALAB_API_KEY.",
            f"وُجدت بطاقة {gpu_name or 'رسومات'} بذاكرة {vram / 1024:.0f} غيغابايت. "
            "شغّل خادم vLLM (الأفضل) أو ثبّت إضافة Hugging Face مع CHANDRA_ENGINE=hf. "
            "يمكن أيضاً وضع مفتاح DATALAB_API_KEY.",
        )
    if vram > 0:
        return _none(
            f"{gpu_name} reports {vram / 1024:.1f} GB. Chandra in bfloat16 wants about "
            "16 GB. This card is too small for the supported local setup. Set "
            "DATALAB_API_KEY to use Datalab's hosted accurate mode.",
            f"البطاقة {gpu_name} تبلغ ذاكرتها {vram / 1024:.1f} غيغابايت. شاندرا بدقة "
            "bfloat16 يحتاج نحو 16 غيغابايت. استخدم مفتاح DATALAB_API_KEY للوضع المستضاف.",
        )
    return _none(
        "No NVIDIA GPU was found, and DATALAB_API_KEY is empty. Chandra's quality "
        "mode is not practical on CPU. Add an API key, or run this on a machine "
        "with a 16 GB or larger NVIDIA GPU.",
        "لا توجد بطاقة NVIDIA ومفتاح DATALAB_API_KEY فارغ. تشغيل شاندرا على المعالج "
        "وحده غير عملي. أضف مفتاح الواجهة، أو انقل التشغيل إلى جهاز ببطاقة 16 غيغابايت أو أكثر.",
    )


def _none(detail_en: str, detail_ar: str) -> Decision:
    return Decision(
        engine=None,
        ready=False,
        demo=False,
        title_en="No OCR engine yet",
        title_ar="لا يوجد محرّك تعرّف بعد",
        detail_en=detail_en,
        detail_ar=detail_ar,
        speed_en="Nothing will be read until an engine is configured.",
        speed_ar="لن يُقرأ شيء قبل إعداد محرّك.",
    )


def _vllm_decision(reachable: bool, forced: bool) -> Decision:
    if not reachable:
        return Decision(
            engine="vllm",
            ready=False,
            demo=False,
            title_en="vLLM is not answering",
            title_ar="خادم vLLM لا يستجيب",
            detail_en=(
                "CHANDRA_ENGINE=vllm, but the server at VLLM_API_BASE did not respond. "
                "Start it with Docker (see the README) and leave this desk running."
            ),
            detail_ar=(
                "المحرك مضبوط على vLLM، لكن الخادم في VLLM_API_BASE لا يجيب. "
                "شغّله عبر Docker كما في دليل التشغيل."
            ),
            speed_en="Idle until the model server is up. The first start also downloads about 10 GB of weights.",
            speed_ar="بانتظار الخادم. التشغيل الأول ينزّل نحو 10 غيغابايت من الأوزان.",
        )
    return Decision(
        engine="vllm",
        ready=True,
        demo=False,
        title_en="Local Chandra · vLLM",
        title_ar="شاندرا محلياً · vLLM",
        detail_en=(
            "Best local setup: bfloat16, layout prompt, 12,384 output tokens. "
            "Pages are sent one at a time so a desktop GPU does not run out of memory."
            if not forced
            else "Using the vLLM server you configured."
        ),
        detail_ar=(
            "أفضل إعداد محلي: دقة bfloat16، مع الحفاظ على التخطيط، و12384 رمزاً للخرج. "
            "تُرسل الصفحات واحدة واحدة حتى لا تمتلئ ذاكرة البطاقة."
        ),
        speed_en=(
            "On a 24 GB card, expect roughly 10–40 seconds a page. "
            "An H100 in Chandra's own benchmark does about 1.4 pages a second when many pages run at once; this desk stays at one page at a time on purpose."
        ),
        speed_ar=(
            "على بطاقة 24 غيغابايت، نحو 10–40 ثانية للصفحة. "
            "على H100 يبلغ قياس شاندرا حوالي 1.4 صفحة في الثانية مع تشغيل متوازٍ؛ هذا المكتب يقرأ صفحة واحدة في كل مرة عمداً."
        ),
    )


def _hf_decision(
    vram: int, gpu_name: str, installed: bool, forced: bool, allow_cpu: bool = False
) -> Decision:
    if not installed:
        return Decision(
            engine="hf",
            ready=False,
            demo=False,
            title_en="Hugging Face extra is not installed",
            title_ar="إضافة Hugging Face غير مثبّتة",
            detail_en="Install it with: pip install 'chandra-ocr[hf]'",
            detail_ar="ثبّتها بالأمر: pip install 'chandra-ocr[hf]'",
            speed_en="Slower than vLLM. One page at a time, still bfloat16.",
            speed_ar="أبطأ من vLLM. صفحة واحدة في كل مرة، وبدقة bfloat16.",
        )
    if vram == 0:
        if not (forced and allow_cpu):
            return Decision(
                engine="hf",
                ready=False,
                demo=False,
                title_en="No GPU for local Chandra",
                title_ar="لا توجد بطاقة لتشغيل شاندرا محلياً",
                detail_en=(
                    "The Hugging Face backend has nowhere to run. CPU mode stays off: "
                    "Chandra 2 in bfloat16 needs about 12 GB of RAM and still takes many "
                    "minutes a page, when it finishes at all. Use a 16 GB NVIDIA GPU, or "
                    "set DATALAB_API_KEY. ALLOW_CPU=1 opts into the slow path anyway."
                ),
                detail_ar=(
                    "وضع المعالج متوقف. شاندرا 2 بدقة bfloat16 يحتاج نحو 12 غيغابايت "
                    "وقد يستغرق دقائق طويلة للصفحة. استخدم بطاقة 16 غيغابايت أو مفتاح Datalab. "
                    "ALLOW_CPU=1 يشغّل المسار البطيء على مسؤوليتك."
                ),
                speed_en="Not running.",
                speed_ar="غير شغّال.",
            )
        return Decision(
            engine="hf",
            ready=True,
            demo=False,
            title_en="Chandra on CPU",
            title_ar="شاندرا على المعالج",
            detail_en=(
                "ALLOW_CPU=1. This will try bfloat16 on the CPU. A single dense page can "
                "take many minutes and the process may be killed when RAM runs out."
            ),
            detail_ar=(
                "ALLOW_CPU=1. المحاولة على المعالج بدقة bfloat16. الصفحة الكثيفة قد تأخذ "
                "دقائق طويلة، وقد تتوقف العملية إذا امتلأت الذاكرة."
            ),
            speed_en="Many minutes per page. Often too slow to be useful.",
            speed_ar="دقائق كثيرة للصفحة. غالباً أبطأ مما ينفع.",
        )
    if vram < 12000 and not forced:
        return _none(
            f"{gpu_name} has {vram / 1024:.1f} GB, which is tight for bfloat16.",
            f"ذاكرة {gpu_name} تبلغ {vram / 1024:.1f} غيغابايت، وهذا ضيق لدقة bfloat16.",
        )
    note = ""
    if vram and vram < 14000:
        note = " This card is close to the limit; if it runs out of memory, use the Datalab API instead."
    return Decision(
        engine="hf",
        ready=True,
        demo=False,
        title_en="Local Chandra · Hugging Face",
        title_ar="شاندرا محلياً · Hugging Face",
        detail_en=(
            "Running datalab-to/chandra-ocr-2 in bfloat16 through Transformers, "
            "one page at a time, with the layout prompt."
            + note
        ),
        detail_ar=(
            "تشغيل datalab-to/chandra-ocr-2 بدقة bfloat16 عبر Transformers، "
            "صفحة واحدة في كل مرة، مع الحفاظ على التخطيط."
        ),
        speed_en="Often slower than vLLM on the same GPU: plan on half a minute to a few minutes a page.",
        speed_ar="غالباً أبطأ من vLLM على البطاقة نفسها: من نصف دقيقة إلى بضع دقائق للصفحة.",
    )


def _datalab_decision(configured: bool, forced: bool, gpu_idle: bool = False) -> Decision:
    if not configured:
        return Decision(
            engine="datalab",
            ready=False,
            demo=False,
            title_en="Datalab API key missing",
            title_ar="مفتاح Datalab غير موجود",
            detail_en="Set DATALAB_API_KEY in .env. The key stays on this computer; pages are sent to Datalab.",
            detail_ar="ضع DATALAB_API_KEY في ملف .env. المفتاح يبقى هنا، والصفحات تُرسل إلى Datalab.",
            speed_en="Hosted accurate mode. Needs a network connection.",
            speed_ar="الوضع المستضاف الأدق. يحتاج اتصالاً بالشبكة.",
        )
    idle = ""
    if gpu_idle:
        idle = " A GPU is present but the local runtime is not installed, so this desk is using the hosted API."
    return Decision(
        engine="datalab",
        ready=True,
        demo=False,
        title_en="Datalab hosted API",
        title_ar="واجهة Datalab المستضافة",
        detail_en=(
            "No local weights. Documents are uploaded to Datalab with mode=accurate, "
            "then deleted from their side according to your Datalab account settings. "
            "Their hosted model is a bit stronger overall than the open weights."
            + idle
        ),
        detail_ar=(
            "من دون أوزان محلية. تُرفع المستندات إلى Datalab بوضع accurate. "
            "النموذج المستضاف أقوى قليلاً في مجمل الاختبارات من الأوزان المفتوحة."
        ),
        speed_en="Usually seconds to a couple of minutes a document, depending on their queue and the page count. Your files leave this computer.",
        speed_ar="غالباً من ثوانٍ إلى دقيقتين للمستند، حسب الطابور وعدد الصفحات. الملفات تغادر هذا الجهاز.",
    )
