"""Quality defaults taken from Chandra's own CLI and vLLM launcher.

The open model never looks at more than 3072×2048 pixels (6,291,456), which is
also the ``max_pixels`` value Chandra passes to vLLM. Rendering above that only
costs memory. Token budget, prompt, and dtype match the upstream defaults that
Datalab uses for their published benchmarks.
"""

PROMPT_TYPE = "ocr_layout"
DEFAULT_MAX_OUTPUT_TOKENS = 12384
IMAGE_DPI = 192
MIN_PDF_IMAGE_DIM = 1024
MIN_IMAGE_DIM = 1536
INFERENCE_MAX_PIXELS = 3072 * 2048
SAFE_DECODE_PIXELS = 32_000_000
INCLUDE_IMAGES = True
VLLM_IMAGE = "vllm/vllm-openai:v0.17.0"
VLLM_DTYPE = "bfloat16"
VLLM_MAX_MODEL_LEN = 18000
