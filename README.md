# AOcr

A self-hosted desk for [Chandra OCR 2](https://github.com/datalab-to/chandra) by Datalab. Drop a PDF or a photo, including Arabic documents, and read it back as Markdown, HTML, JSON, or plain text. Layout, tables, math, and handwriting are kept the way Chandra keeps them.

The desk runs on your computer. Your phone, on the same Wi-Fi, opens it in a browser. Nothing is uploaded anywhere unless you deliberately choose Datalab's hosted API.

## البدء السريع

يلزم Python 3.10 أو أحدث، وNode.js 20 أو أحدث.

```bash
cp .env.example .env
./run.sh
```

عند الإقلاع تطبع الشاشة عنوانين:

- على هذا الجهاز: `http://127.0.0.1:8787`
- على الهاتف: `http://عنوان-الجهاز-في-الشبكة:8787`

افتح الرابط الثاني من هاتفك وأنتما على شبكة الواي فاي نفسها. لا تستخدم `localhost` من الهاتف. إذا لم تُفتح الصفحة، اسمح للمنفذ `8787` في الجدار الناري.

من دون بطاقة NVIDIA بذاكرة قريبة من 16 غيغابايت، ضع مفتاح Datalab في ملف `.env` ثم أعد التشغيل:

```
DATALAB_API_KEY=your-key
CHANDRA_ENGINE=datalab
```

التشغيل على المعالج وحده غير عملي مع هذا النموذج. التفاصيل في القسم التالي.

على Windows يمكن تشغيل `./run.sh` من Git Bash، أو `powershell -ExecutionPolicy Bypass -File .\run.ps1`.

## What this expects from the machine

Chandra 2 (`datalab-to/chandra-ocr-2`) is a vision model of about 5.3 billion parameters. Datalab's own launcher runs it in **bfloat16**. That is the setting this desk uses. It is also the setting behind their published benchmarks.

| Machine | What happens |
| --- | --- |
| NVIDIA GPU, 16 GB or more | This is the supported local setup. A 24 GB card such as a 4090 is comfortable. |
| NVIDIA GPU, 12 GB | Too tight for the supported bfloat16 setup. Use Datalab's API, or try vLLM only after lowering `VLLM_MAX_MODEL_LEN` (for example to `8192`) and expect out-of-memory errors. |
| No NVIDIA GPU | Use `DATALAB_API_KEY`. CPU mode stays off. |
| CPU, `ALLOW_CPU=1` and `CHANDRA_ENGINE=hf` | The desk will try. A dense page can take many minutes and the process may be killed when RAM runs out. This is not a quality mode. |

Weights are about 10 GB and download on the first real run. They are not in this repository.

**Speed, honestly.** This desk reads one page at a time so a desktop card does not run out of memory. On a 24 GB GPU, expect roughly 10–40 seconds a page, longer for a dense table or a full page of handwriting. Chandra's own vLLM benchmark on an H100, with many pages in flight, is about 1.4 pages a second (around 60 seconds average latency under that load). They estimate closer to 2 pages a second on ordinary documents when the server is allowed to batch. We do not batch, on purpose.

The Hugging Face backend is the same model and the same prompt, still bfloat16, and usually slower than vLLM.

Datalab's hosted `accurate` mode is a bit stronger than the open weights on their overall benchmark (86.7 versus 85.8). On their Arabic set the open Chandra 2 score is 68.4% and the API is 67.6%, so for Arabic they are close. Hosted mode needs a network connection, and the file leaves this computer.

## Run it

```bash
cp .env.example .env
./run.sh
```

The first launch creates a virtualenv, installs the Python package and the web UI, then prints:

```text
  This computer    http://127.0.0.1:8787
  Your phone       http://192.168.1.20:8787
```

The exact phone address is your computer's LAN address. Both the API and the web server listen on `0.0.0.0`, so the phone can reach them. Leave the terminal open.

`CHANDRA_DEV=1 ./run.sh` skips the production build and uses the development server. Use that while you are changing the UI. The normal command builds first, then serves the built UI.

### On your phone

1. Connect the phone to the same Wi-Fi as the computer. A guest network often blocks this. So does a VPN on the computer.
2. Open the `http://192.168…:8787` address printed at startup. It is also shown at the bottom of the desk.
3. If the page does not load, allow TCP port **8787**:
   - Linux with ufw: `sudo ufw allow 8787/tcp`
   - macOS: accept the incoming-connection prompt for Node
   - Windows: allow the port in Windows Defender Firewall

`localhost` on the phone is the phone itself. It will not open the desk.

### From outside the house

[cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) can publish the desk for a quick test:

```bash
cloudflared tunnel --url http://127.0.0.1:8787
```

It prints a temporary public URL. That URL is reachable by anyone who has it, and the traffic passes through Cloudflare. Do not use it for documents you would not want to leave the house. Shut it down when you are done. The desk does not start a tunnel by itself.

### Docker

```bash
cp .env.example .env
docker compose up --build
```

Open `http://<this-computer-lan-ip>:8787`. Set `ADVERTISED_HOSTS` in `.env` to that LAN address if you want the on-screen phone link to be right; inside Docker the container cannot always see it.

With an NVIDIA GPU, driver, and [nvidia-container-toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html):

```bash
docker compose --profile gpu up --build
```

That starts vLLM with Chandra's quality flags: bfloat16, context 18000, image budget 6,291,456 pixels, prefix caching, and the served name `chandra`. The first start is a long download. On a 16 GB card, if vLLM runs out of memory, set `VLLM_MAX_MODEL_LEN=8192` and start again.

Give vLLM a few minutes, then refresh the desk. `auto` switches to it once `http://vllm:8000/v1` answers.

## How a document is read

- The browser slices the file into 8 MB pieces and uploads them one at a time, so a large PDF is never held as a single request.
- PDF pages are rendered one at a time at Chandra's own raster settings (192 DPI, and at least 1024 pixels on the short side), then capped at 3072×2048 pixels. That is the largest image the model actually looks at. Bigger renders only cost memory.
- Photos larger than about 32 megapixels are scaled with ffmpeg before they are decoded, so a poster-sized scan does not have to fit in RAM at full size. Install ffmpeg if you have those. Ordinary phone photos and PDFs do not need it.
- A single background worker reads the queue. Page one shows up while later pages are still running. Refreshing the browser does not lose the job.
- The default limit is 5,000 pages and 2 GB. Raise `MAX_PAGES` or `MAX_UPLOAD_BYTES` if you need more.

Local Chandra is called the way the upstream CLI calls it for a careful read:

- prompt `ocr_layout` (blocks, bounding boxes, tables, math, forms, handwriting)
- up to 12,384 output tokens a page
- images and figures included
- page headers and footers left out, unless you tick the box (that is Chandra's own default)
- vLLM temperature 0 and the upstream retry when a page starts repeating itself

The hosted fallback calls `POST https://www.datalab.to/api/v1/convert` with `mode=accurate`, `exact_transcription=true`, and Markdown, HTML, and JSON together.

## Engines

| `CHANDRA_ENGINE` | When to use it |
| --- | --- |
| `auto` | Default. vLLM if it is up, else a local GPU with the Hugging Face extra, else Datalab if the key is set. |
| `vllm` | Best local path. Start the server with the compose profile above, or with `chandra_vllm` from the Chandra repo. |
| `hf` | `pip install 'chandra-ocr[hf]'` (the run script does this when the engine is `hf`). Slower. Still bfloat16, still one page at a time. |
| `datalab` | No suitable GPU. Set `DATALAB_API_KEY`. |
| `mock` | Sample text only, for trying the desk with no model. The screen says so. It is not OCR. |

`mock` never turns on by itself.

## Try it

The desk includes a two-page sample, Arabic then English. Use **Try the sample note**, or open `web/public/sample-note.pdf`. Each page and the whole document can be copied or downloaded as Markdown, HTML, JSON, plain text, or a zip of all four.

The reading view follows the script. An Arabic page is right-to-left even when the buttons are in English. Switch the interface to العربية from the top corner.

## Tests

The model is not loaded. Tests drive the real upload, queue, and export path with a stand-in engine, and they check that Chandra is called with the layout prompt and the 12,384-token budget.

```bash
python3 -m venv .venv
.venv/bin/pip install -e "./server[dev]"
.venv/bin/pytest server/tests
```

## License

- **This desk** is Apache-2.0. See `LICENSE`.
- **Chandra's code** (`chandra-ocr`) is Apache-2.0, copyright Datalab: <https://github.com/datalab-to/chandra>.
- **The weights** `datalab-to/chandra-ocr-2` use a modified OpenRAIL-M license. Personal use, research, and startups under $2M in revenue or funding are allowed. You may not use the model to compete with Datalab's API. Broader commercial or on-prem use needs a license from Datalab: <https://www.datalab.to/pricing>. The full text is in `licenses/CHANDRA_MODEL_OPENRAIL.txt`, including the use restrictions and the share-alike condition. Read it. This repository does not ship the weights.
- The **hosted API** is Datalab's service, under their terms. Files you send that way leave this computer.

## Troubleshooting

**The page says there is no engine.** No NVIDIA GPU was found and `DATALAB_API_KEY` is empty, or vLLM was selected and is not answering. Set the key, or start the GPU profile and wait until the model has loaded.

**vLLM exits at once.** The container needs a GPU. `docker compose up` without `--profile gpu` does not start it. `could not select device driver nvidia` means the NVIDIA container toolkit is missing, or you are on a machine without an NVIDIA GPU. There is no ROCm setup in this repo.

**The first real page takes a long time, then fails.** The weights are still downloading, or the card ran out of memory. Lower `VLLM_MAX_MODEL_LEN` and restart vLLM. Hugging Face on a 16 GB card can also fail if something else is using the GPU.

**A page comes back empty or repeats a phrase.** Chandra already retries that. If it still fails, that page is marked and the rest of the document continues.

**Arabic in the result is left-to-right.** The reading pane chooses direction from the letters on the page. A page that is mostly English stays left-to-right. Switch to the page that is actually Arabic, or open the whole document.

**The phone cannot connect.** Same Wi-Fi, not a guest network, no VPN, the printed LAN address, port 8787 allowed. Confirm from the computer with `curl -s http://127.0.0.1:8787` and from the phone with the LAN address.

**`ffmpeg` errors on a huge scan.** Install ffmpeg (`brew install ffmpeg`, `sudo apt install ffmpeg`) or downscale the image. PDFs do not use this path.

**Uploads stop at a few hundred megabytes.** A proxy in front of the desk may be limiting body size. The built-in server accepts the chunk size in `.env` (`CHUNK_SIZE`, default 8 MB).

**I want the desk locked on a shared network.** Set `ACCESS_TOKEN` in `.env` and restart. The browser asks for it once per tab.
