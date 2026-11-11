export type Health = {
  ok: boolean;
  version: string;
  engine: string | null;
  engine_ready: boolean;
  demo: boolean;
  auth_required: boolean;
  title_en: string;
  title_ar: string;
  detail_en: string;
  detail_ar: string;
  speed_en: string;
  speed_ar: string;
  gpu: { name: string; memory_mib: number; driver: string }[];
  vllm_reachable: boolean;
  datalab_configured: boolean;
  hf_installed: boolean;
  lan_urls: string[];
  chunk_size: number;
  max_upload_bytes: number;
  max_pages: number;
};

export type PageSummary = {
  index: number;
  ready: boolean;
  error: string | null;
  token_count: number;
  width: number | null;
  height: number | null;
  has_preview: boolean;
};

export type Job = {
  id: string;
  filename: string;
  status: "queued" | "processing" | "complete" | "failed" | "cancelled";
  phase: string;
  engine: string | null;
  pages_total: number;
  pages_done: number;
  message: string | null;
  error: string | null;
  warnings: string[];
  include_headers_footers: boolean;
  created_at: number;
  pages: PageSummary[];
};

export type PageBody = {
  index: number;
  markdown: string;
  html: string;
  plain: string;
  chunks: unknown[];
  token_count: number;
  error: string | null;
};

type UploadInit = {
  id: string;
  chunk_size: number;
  parts: number;
};

const TOKEN_KEY = "aocr-token";

export function getToken(): string {
  if (typeof window === "undefined") return "";
  return sessionStorage.getItem(TOKEN_KEY) || "";
}

export function setToken(value: string) {
  sessionStorage.setItem(TOKEN_KEY, value.trim());
}

export function authHeaders(extra?: HeadersInit): Headers {
  const headers = new Headers(extra);
  const token = getToken();
  if (token) headers.set("X-Access-Token", token);
  return headers;
}

export function errorMessage(body: unknown, lang: "en" | "ar"): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object") {
      const record = detail as { en?: string; ar?: string };
      return (lang === "ar" ? record.ar : record.en) || record.en || record.ar || "";
    }
  }
  return lang === "ar" ? "حدث خطأ غير متوقع." : "Something went wrong.";
}

async function readBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text };
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, headers: authHeaders(init?.headers) });
  const body = await readBody(response);
  if (!response.ok) {
    const error = new Error("request failed") as Error & { status: number; body: unknown };
    error.status = response.status;
    error.body = body;
    throw error;
  }
  return body as T;
}

export function getHealth() {
  return api<Health>("/api/health");
}

export function listJobs() {
  return api<{ jobs: Job[] }>("/api/jobs");
}

export function getJob(id: string) {
  return api<Job>(`/api/jobs/${id}`);
}

export function getPage(id: string, index: number) {
  return api<PageBody>(`/api/jobs/${id}/pages/${index}`);
}

export function cancelJob(id: string) {
  return api<Job>(`/api/jobs/${id}/cancel`, { method: "POST" });
}

export function deleteJob(id: string) {
  return api<{ deleted: string }>(`/api/jobs/${id}`, { method: "DELETE" });
}

export async function exportText(id: string, format: string, page: number | "all") {
  const payload = await api<{ filename: string; content: string }>(
    `/api/jobs/${id}/export?format=${format}&page=${page}&download=0`,
  );
  return payload;
}

export async function downloadExport(id: string, format: string, page: number | "all") {
  const response = await fetch(`/api/jobs/${id}/export?format=${format}&page=${page}&download=1`, {
    headers: authHeaders(),
  });
  if (!response.ok) {
    throw new Error("download failed");
  }
  const blob = await response.blob();
  const disposition = response.headers.get("Content-Disposition") || "";
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/);
  const name = encoded ? decodeURIComponent(encoded[1].replace(/["']/g, "")) : `document.${format}`;
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

export async function uploadFile(
  file: File,
  includeHeaders: boolean,
  onProgress: (fraction: number) => void,
): Promise<Job> {
  const init = await api<UploadInit>("/api/uploads", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      filename: file.name,
      size: file.size,
      content_type: file.type || "application/octet-stream",
    }),
  });
  for (let index = 0; index < init.parts; index += 1) {
    const start = index * init.chunk_size;
    const blob = file.slice(start, Math.min(file.size, start + init.chunk_size));
    const response = await fetch(`/api/uploads/${init.id}/chunks/${index}`, {
      method: "PUT",
      headers: authHeaders(),
      body: blob,
    });
    if (!response.ok) {
      const body = await readBody(response);
      const error = new Error("chunk failed") as Error & { body: unknown };
      error.body = body;
      throw error;
    }
    onProgress((index + 1) / init.parts);
  }
  return api<Job>(`/api/uploads/${init.id}/complete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ include_headers_footers: includeHeaders }),
  });
}
