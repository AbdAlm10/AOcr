"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import { Check, Copy, Download, FileUp } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  type Health,
  type Job,
  cancelJob,
  deleteJob,
  downloadExport,
  errorMessage,
  exportText,
  getHealth,
  getJob,
  getPage,
  listJobs,
  setToken,
  uploadFile,
} from "@/lib/api";
import { messages, type Lang } from "@/lib/i18n";
import { htmlFragment, readingDirection } from "@/lib/utils";

type Scope = "page" | "document";
type View = { html: string; markdown: string; plain: string; json: string; error: string | null };

export function Desk() {
  const [lang, setLang] = useState<Lang>("en");
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState(false);
  const [tokenInput, setTokenInput] = useState("");
  const [authFailed, setAuthFailed] = useState(false);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [pageIndex, setPageIndex] = useState(0);
  const [scope, setScope] = useState<Scope>("page");
  const [view, setView] = useState<View | null>(null);
  const [dragging, setDragging] = useState(false);
  const [uploadFraction, setUploadFraction] = useState<number | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [keepHeaders, setKeepHeaders] = useState(false);
  const [copied, setCopied] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const copy = messages[lang];

  useEffect(() => {
    const stored = window.localStorage.getItem("aocr-lang");
    if (stored === "ar" || stored === "en") setLang(stored);
  }, []);

  useEffect(() => {
    document.documentElement.lang = lang;
    document.documentElement.dir = lang === "ar" ? "rtl" : "ltr";
    window.localStorage.setItem("aocr-lang", lang);
  }, [lang]);

  const refreshHealth = useCallback(async () => {
    try {
      const next = await getHealth();
      setHealth(next);
      setHealthError(false);
      setAuthFailed(false);
    } catch (error) {
      const status = (error as { status?: number }).status;
      if (status === 401) setAuthFailed(true);
      else setHealthError(true);
    }
  }, []);

  useEffect(() => {
    void refreshHealth();
    const timer = window.setInterval(() => void refreshHealth(), 15000);
    return () => window.clearInterval(timer);
  }, [refreshHealth]);

  const refreshJobs = useCallback(async () => {
    if (!health?.engine_ready && health?.auth_required) return;
    try {
      const data = await listJobs();
      setJobs(data.jobs);
    } catch {
      /* The desk can still accept a file if the list fails once. */
    }
  }, [health?.auth_required, health?.engine_ready]);

  useEffect(() => {
    if (health && !health.auth_required) void refreshJobs();
    if (health && health.auth_required && !authFailed) void refreshJobs();
  }, [health, authFailed, refreshJobs]);

  useEffect(() => {
    if (!activeId) return;
    let stop = false;
    const tick = async () => {
      try {
        const next = await getJob(activeId);
        if (!stop) setJob(next);
        if (next.status === "queued" || next.status === "processing") {
          window.setTimeout(tick, 700);
        } else if (!stop) {
          void refreshJobs();
        }
      } catch {
        if (!stop) window.setTimeout(tick, 1200);
      }
    };
    void tick();
    return () => {
      stop = true;
    };
  }, [activeId, refreshJobs]);

  const signature = `${job?.id ?? ""}:${job?.pages_done ?? 0}:${job?.status ?? ""}:${pageIndex}:${scope}`;

  useEffect(() => {
    if (!job) {
      setView(null);
      return;
    }
    let cancel = false;
    const load = async () => {
      try {
        if (scope === "page") {
          const summary = job.pages.find((page) => page.index === pageIndex);
          if (!summary?.ready) {
            if (!cancel) setView(null);
            return;
          }
          const page = await getPage(job.id, pageIndex);
          if (cancel) return;
          setView({
            html: page.html,
            markdown: page.markdown,
            plain: page.plain,
            json: JSON.stringify(
              {
                page: pageIndex + 1,
                markdown: page.markdown,
                html: page.html,
                text: page.plain,
                chunks: page.chunks,
                token_count: page.token_count,
                error: page.error,
              },
              null,
              2,
            ),
            error: page.error,
          });
          return;
        }
        const [markdown, html, plain, json] = await Promise.all([
          exportText(job.id, "md", "all"),
          exportText(job.id, "html", "all"),
          exportText(job.id, "txt", "all"),
          exportText(job.id, "json", "all"),
        ]);
        if (!cancel) {
          setView({
            html: html.content,
            markdown: markdown.content,
            plain: plain.content,
            json: json.content,
            error: null,
          });
        }
      } catch {
        if (!cancel) setView(null);
      }
    };
    void load();
    return () => {
      cancel = true;
    };
    // signature already covers the fields that should refetch
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature]);

  const send = useCallback(
    async (file: File) => {
      setNotice(null);
      setUploadFraction(0);
      try {
        const created = await uploadFile(file, keepHeaders, setUploadFraction);
        setActiveId(created.id);
        setJob(created);
        setPageIndex(0);
        setScope("page");
        await refreshJobs();
      } catch (error) {
        const body = (error as { body?: unknown }).body;
        setNotice(errorMessage(body, lang));
      } finally {
        setUploadFraction(null);
      }
    },
    [keepHeaders, lang, refreshJobs],
  );

  const onFiles = useCallback(
    (list: FileList | null) => {
      const file = list?.[0];
      if (file) void send(file);
    },
    [send],
  );

  const openSample = useCallback(async () => {
    const response = await fetch("/sample-note.pdf");
    const blob = await response.blob();
    await send(new File([blob], "sample-note.pdf", { type: "application/pdf" }));
  }, [send]);

  const progressLabel = useMemo(() => {
    if (!job) return "";
    if (job.status === "failed") return copy.failed;
    if (job.status === "cancelled") return copy.cancelled;
    if (job.status === "queued") return copy.waiting;
    if (job.phase === "preview") return copy.preparing;
    if (job.phase === "remote") return copy.remote;
    if (job.pages_total > 0) return copy.readingPage(job.pages_done, job.pages_total);
    return copy.preparing;
  }, [copy, job]);

  const fraction = job && job.pages_total > 0 ? Math.min(1, job.pages_done / job.pages_total) : 0;
  const busy = job?.status === "queued" || job?.status === "processing";
  const textForCopy = view ? view.markdown || view.plain : "";
  const direction = readingDirection(`${view?.plain || ""} ${view?.markdown || ""}`);

  async function copyActive() {
    if (!textForCopy) return;
    await navigator.clipboard.writeText(textForCopy);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  }

  if (healthError) {
    return (
      <Shell lang={lang} setLang={setLang}>
        <Card>
          <h1 className="text-2xl font-semibold tracking-tight">{copy.apiDown}</h1>
          <p className="mt-3 text-[#6e6e73]">{copy.apiDownBody}</p>
        </Card>
      </Shell>
    );
  }

  if (!health) {
    return (
      <Shell lang={lang} setLang={setLang}>
        <div className="h-40 animate-pulse rounded-[28px] bg-white" />
      </Shell>
    );
  }

  if (health.auth_required && authFailed) {
    return (
      <Shell lang={lang} setLang={setLang}>
        <Card>
          <h1 className="text-2xl font-semibold tracking-tight">{copy.locked}</h1>
          <p className="mt-3 text-[#6e6e73]">{copy.lockedBody}</p>
          <form
            className="mt-6 flex flex-col gap-3 sm:flex-row"
            onSubmit={(event) => {
              event.preventDefault();
              setToken(tokenInput);
              setAuthFailed(false);
              void refreshHealth();
            }}
          >
            <input
              type="password"
              value={tokenInput}
              onChange={(event) => setTokenInput(event.target.value)}
              className="h-12 flex-1 rounded-2xl bg-[#f5f5f7] px-4 outline-none ring-1 ring-transparent focus:ring-[#0071e3]/40"
              autoFocus
            />
            <Button type="submit" size="lg">
              {copy.unlock}
            </Button>
          </form>
        </Card>
      </Shell>
    );
  }

  const engineTitle = lang === "ar" ? health.title_ar : health.title_en;
  const engineDetail = lang === "ar" ? health.detail_ar : health.detail_en;
  const engineSpeed = lang === "ar" ? health.speed_ar : health.speed_en;

  return (
    <Shell lang={lang} setLang={setLang} engine={engineTitle} ready={health.engine_ready} demo={health.demo}>
      <p className="max-w-2xl text-[15px] leading-6 text-[#6e6e73]">{engineDetail}</p>
      <p className="mt-1 max-w-2xl text-[13px] leading-5 text-[#86868b]">{engineSpeed}</p>

      {notice && <p className="mt-4 text-sm text-[#b42318]">{notice}</p>}

      {!health.engine_ready ? (
        <Card className="mt-8">
          <h2 className="text-xl font-semibold tracking-tight">{engineTitle}</h2>
          <p className="mt-3 max-w-xl text-[#6e6e73]">{engineDetail}</p>
        </Card>
      ) : job ? (
        <Workspace
          copy={copy}
          job={job}
          pageIndex={pageIndex}
          setPageIndex={setPageIndex}
          scope={scope}
          setScope={setScope}
          view={view}
          direction={direction}
          progressLabel={progressLabel}
          fraction={fraction}
          busy={busy}
          copied={copied}
          onCopy={() => void copyActive()}
          onDownload={(format) => void downloadExport(job.id, format, scope === "page" ? pageIndex : "all")}
          onBack={() => {
            setActiveId(null);
            setJob(null);
          }}
          onCancel={() => void cancelJob(job.id)}
        />
      ) : (
        <Dropzone
          copy={copy}
          dragging={dragging}
          setDragging={setDragging}
          uploadFraction={uploadFraction}
          keepHeaders={keepHeaders}
          setKeepHeaders={setKeepHeaders}
          inputRef={inputRef}
          onFiles={onFiles}
          onSample={() => void openSample()}
        />
      )}

      {!job && jobs.length > 0 && (
        <section className="mt-10">
          <h2 className="text-sm font-medium text-[#6e6e73]">{copy.recent}</h2>
          <ul className="mt-3 divide-y divide-black/5 overflow-hidden rounded-[24px] bg-white ring-1 ring-black/5">
            {jobs.map((item) => (
              <li key={item.id} className="flex items-center gap-3 px-4 py-3">
                <button type="button" className="min-w-0 flex-1 text-start" onClick={() => openJob(item)}>
                  <span className="block truncate font-medium">{item.filename}</span>
                  <span className="text-[13px] text-[#6e6e73]">
                    {item.status} · {item.pages_done}/{item.pages_total || "–"}
                  </span>
                </button>
                <button
                  type="button"
                  className="text-[13px] text-[#6e6e73]"
                  onClick={() => void deleteJob(item.id).then(() => refreshJobs())}
                >
                  {copy.remove}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {health.lan_urls.length > 0 && (
        <section className="mt-12 rounded-[24px] bg-white px-5 py-4 ring-1 ring-black/5">
          <h2 className="text-sm font-medium">{copy.phone}</h2>
          <p className="mt-1 text-[13px] text-[#6e6e73]">{copy.phoneBody}</p>
          <ul className="mt-2 space-y-1">
            {health.lan_urls.map((url) => (
              <li key={url}>
                <a className="text-[#0071e3]" href={url} dir="ltr">
                  {url}
                </a>
              </li>
            ))}
          </ul>
        </section>
      )}
    </Shell>
  );

  function openJob(item: Job) {
    setActiveId(item.id);
    setJob(item);
    setPageIndex(0);
    setScope("page");
  }
}

function Shell({
  children,
  lang,
  setLang,
  engine,
  ready,
  demo,
}: {
  children: ReactNode;
  lang: Lang;
  setLang: (lang: Lang) => void;
  engine?: string;
  ready?: boolean;
  demo?: boolean;
}) {
  const copy = messages[lang];
  return (
    <div className="mx-auto min-h-dvh max-w-6xl px-4 py-6 sm:px-6 sm:py-8">
      <header className="mb-8 flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-[13px] font-medium tracking-wide text-[#6e6e73]">Chandra OCR 2</p>
          <h1 className="mt-1 text-[2rem] font-semibold tracking-tight sm:text-[2.4rem]">{copy.title}</h1>
          <p className="mt-1 max-w-xl text-[#6e6e73]">{copy.subtitle}</p>
        </div>
        <div className="flex items-center gap-2">
          {engine && (
            <span className="inline-flex items-center gap-2 rounded-full bg-white px-3 py-1.5 text-[13px] ring-1 ring-black/5">
              <span
                className={`h-1.5 w-1.5 rounded-full ${ready ? (demo ? "bg-[#c48a12]" : "bg-[#1f8a4c]") : "bg-[#b42318]"}`}
              />
              {engine}
            </span>
          )}
          <div className="flex rounded-full bg-white p-1 ring-1 ring-black/5">
            <LangButton active={lang === "en"} onClick={() => setLang("en")}>
              English
            </LangButton>
            <LangButton active={lang === "ar"} onClick={() => setLang("ar")}>
              العربية
            </LangButton>
          </div>
        </div>
      </header>
      {children}
    </div>
  );
}

function LangButton({
  active,
  children,
  onClick,
}: {
  active: boolean;
  children: ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-full px-3 py-1.5 text-[13px] ${active ? "bg-[#1d1d1f] text-white" : "text-[#6e6e73]"}`}
    >
      {children}
    </button>
  );
}

function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-[28px] bg-white p-6 shadow-[0_12px_40px_rgba(0,0,0,0.05)] ring-1 ring-black/5 sm:p-8 ${className}`}>
      {children}
    </section>
  );
}

function Dropzone({
  copy,
  dragging,
  setDragging,
  uploadFraction,
  keepHeaders,
  setKeepHeaders,
  inputRef,
  onFiles,
  onSample,
}: {
  copy: (typeof messages)["en"];
  dragging: boolean;
  setDragging: (value: boolean) => void;
  uploadFraction: number | null;
  keepHeaders: boolean;
  setKeepHeaders: (value: boolean) => void;
  inputRef: RefObject<HTMLInputElement | null>;
  onFiles: (list: FileList | null) => void;
  onSample: () => void;
}) {
  return (
    <div className="mt-8">
      <label
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          onFiles(event.dataTransfer.files);
        }}
        className={`flex min-h-72 cursor-pointer flex-col items-center justify-center rounded-[32px] bg-white px-6 py-12 text-center shadow-[0_12px_40px_rgba(0,0,0,0.05)] ring-1 ring-black/5 transition ${dragging ? "ring-2 ring-[#0071e3]" : ""}`}
      >
        <span className="flex h-14 w-14 items-center justify-center rounded-2xl bg-[#f5f5f7] text-[#1d1d1f]">
          <FileUp className="h-6 w-6" />
        </span>
        <span className="mt-5 text-2xl font-semibold tracking-tight">{copy.dropTitle}</span>
        <span className="mt-2 max-w-md text-[15px] leading-6 text-[#6e6e73]">{copy.dropBody}</span>
        <span className="mt-6 inline-flex h-11 items-center rounded-full bg-[#0071e3] px-5 text-sm font-medium text-white">
          {copy.browse}
        </span>
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,.png,.jpg,.jpeg,.webp,.tif,.tiff,.gif,.bmp,application/pdf,image/*"
          className="sr-only"
          onChange={(event) => {
            onFiles(event.target.files);
            event.target.value = "";
          }}
        />
      </label>
      {uploadFraction !== null && (
        <div className="mt-4">
          <div className="mb-1 flex justify-between text-[13px] text-[#6e6e73]">
            <span>{copy.upload}</span>
            <span>{Math.round(uploadFraction * 100)}%</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-white">
            <div className="h-full bg-[#0071e3]" style={{ width: `${uploadFraction * 100}%` }} />
          </div>
        </div>
      )}
      <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <label className="flex items-center gap-2 text-sm text-[#6e6e73]">
          <input
            type="checkbox"
            checked={keepHeaders}
            onChange={(event) => setKeepHeaders(event.target.checked)}
            className="h-4 w-4 accent-[#0071e3]"
          />
          {copy.headers}
        </label>
        <Button type="button" variant="outline" onClick={onSample}>
          {copy.sample}
        </Button>
      </div>
    </div>
  );
}

function Workspace({
  copy,
  job,
  pageIndex,
  setPageIndex,
  scope,
  setScope,
  view,
  direction,
  progressLabel,
  fraction,
  busy,
  copied,
  onCopy,
  onDownload,
  onBack,
  onCancel,
}: {
  copy: (typeof messages)["en"];
  job: Job;
  pageIndex: number;
  setPageIndex: (index: number) => void;
  scope: Scope;
  setScope: (scope: Scope) => void;
  view: View | null;
  direction: "rtl" | "ltr";
  progressLabel: string;
  fraction: number;
  busy: boolean;
  copied: boolean;
  onCopy: () => void;
  onDownload: (format: string) => void;
  onBack: () => void;
  onCancel: () => void;
}) {
  const pageCount = Math.max(job.pages_total, job.pages.length, 1);
  const summary = job.pages.find((page) => page.index === pageIndex);
  return (
    <div className="mt-8">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <button type="button" onClick={onBack} className="text-[13px] text-[#0071e3]">
            {copy.back}
          </button>
          <h2 className="truncate text-xl font-semibold tracking-tight">{job.filename}</h2>
        </div>
        {busy && (
          <Button type="button" variant="outline" size="sm" onClick={onCancel}>
            {copy.stop}
          </Button>
        )}
      </div>
      {(busy || job.status === "failed") && (
        <div className="mb-4">
          <div className="mb-1 text-[13px] text-[#6e6e73]">{progressLabel}</div>
          <div className="h-1.5 overflow-hidden rounded-full bg-white">
            <div
              className={`h-full bg-[#0071e3] ${job.phase === "remote" ? "animate-pulse w-1/3" : ""}`}
              style={job.phase === "remote" ? undefined : { width: `${Math.max(fraction * 100, busy ? 4 : 0)}%` }}
            />
          </div>
          {job.error && <p className="mt-2 text-sm text-[#b42318]">{job.error}</p>}
        </div>
      )}
      {job.warnings.length > 0 && <p className="mb-4 text-sm text-[#6e6e73]">{job.warnings.join(" ")}</p>}
      <div className="grid items-start gap-5 lg:grid-cols-2">
        <section className="rounded-[28px] bg-white p-4 ring-1 ring-black/5 sm:p-5">
          <div className="overflow-hidden rounded-2xl bg-[#f3f3f5]">
            {summary?.has_preview ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={`/api/jobs/${job.id}/pages/${pageIndex}/preview`}
                alt=""
                className="mx-auto max-h-[46vh] w-full object-contain lg:max-h-[72vh]"
              />
            ) : (
              <div className="h-80 animate-pulse bg-[#ececf1]" />
            )}
          </div>
          <div className="mt-4 flex items-center justify-between gap-3">
            <Button type="button" variant="secondary" size="sm" disabled={pageIndex <= 0} onClick={() => setPageIndex(pageIndex - 1)}>
              {copy.previous}
            </Button>
            <span className="text-sm text-[#6e6e73]">
              {copy.page} {pageIndex + 1} {copy.of} {pageCount}
            </span>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={pageIndex + 1 >= pageCount}
              onClick={() => setPageIndex(pageIndex + 1)}
            >
              {copy.next}
            </Button>
          </div>
          {job.pages.length > 1 && (
            <div className="mt-3 flex gap-2 overflow-x-auto pb-1">
              {job.pages.map((page) => (
                <button
                  key={page.index}
                  type="button"
                  onClick={() => setPageIndex(page.index)}
                  className={`h-20 w-14 shrink-0 overflow-hidden rounded-lg bg-[#f3f3f5] ring-2 ${page.index === pageIndex ? "ring-[#0071e3]" : "ring-transparent"}`}
                >
                  {page.has_preview && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={`/api/jobs/${job.id}/pages/${page.index}/preview`} alt="" className="h-full w-full object-cover" />
                  )}
                </button>
              ))}
            </div>
          )}
        </section>
        <section className="rounded-[28px] bg-white p-4 ring-1 ring-black/5 sm:p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex rounded-full bg-[#f5f5f7] p-1">
              <ScopeButton active={scope === "page"} onClick={() => setScope("page")}>
                {copy.thisPage}
              </ScopeButton>
              <ScopeButton active={scope === "document"} onClick={() => setScope("document")}>
                {copy.document}
              </ScopeButton>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button type="button" variant="outline" size="sm" onClick={onCopy} disabled={!view}>
                {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                {copied ? copy.copied : copy.copy}
              </Button>
              {(["md", "html", "json", "txt"] as const).map((format) => (
                <Button key={format} type="button" variant="secondary" size="sm" onClick={() => onDownload(format)}>
                  <Download className="h-3.5 w-3.5" />
                  {format.toUpperCase()}
                </Button>
              ))}
              <Button type="button" variant="secondary" size="sm" onClick={() => onDownload("zip")}>
                <Download className="h-3.5 w-3.5" />
                {copy.all}
              </Button>
            </div>
          </div>
          <Tabs defaultValue="reading" className="mt-4">
            <TabsList>
              <TabsTrigger value="reading">{copy.reading}</TabsTrigger>
              <TabsTrigger value="markdown">{copy.markdown}</TabsTrigger>
              <TabsTrigger value="json">{copy.json}</TabsTrigger>
              <TabsTrigger value="text">{copy.text}</TabsTrigger>
            </TabsList>
            <TabsContent value="reading">
              <ResultFrame>
                {view?.error && <p className="mb-3 text-sm text-[#b42318]">{view.error}</p>}
                {view?.html ? (
                  <div
                    dir={direction}
                    className="reading"
                    dangerouslySetInnerHTML={{ __html: htmlFragment(view.html) }}
                  />
                ) : (
                  <p className="text-[#6e6e73]">{busy ? progressLabel : copy.emptyReading}</p>
                )}
              </ResultFrame>
            </TabsContent>
            <TabsContent value="markdown">
              <ResultFrame>
                <pre dir={direction} className="whitespace-pre-wrap font-sans text-[15px] leading-7">
                  {view?.markdown || (busy ? progressLabel : copy.emptyReading)}
                </pre>
              </ResultFrame>
            </TabsContent>
            <TabsContent value="json">
              <ResultFrame>
                <pre dir="ltr" className="whitespace-pre-wrap font-mono text-[12px] leading-5 text-[#1d1d1f]">
                  {view?.json || (busy ? progressLabel : copy.emptyReading)}
                </pre>
              </ResultFrame>
            </TabsContent>
            <TabsContent value="text">
              <ResultFrame>
                <pre dir={direction} className="whitespace-pre-wrap font-sans text-[15px] leading-7">
                  {view?.plain || (busy ? progressLabel : copy.emptyReading)}
                </pre>
              </ResultFrame>
            </TabsContent>
          </Tabs>
        </section>
      </div>
    </div>
  );
}

function ScopeButton({
  active,
  children,
  onClick,
}: {
  active: boolean;
  children: ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-full px-3 py-1.5 text-[13px] ${active ? "bg-white text-[#1d1d1f] shadow-sm" : "text-[#6e6e73]"}`}
    >
      {children}
    </button>
  );
}

function ResultFrame({ children }: { children: ReactNode }) {
  return <div className="max-h-[68vh] overflow-auto rounded-2xl bg-[#fafafa] px-4 py-4 sm:px-5">{children}</div>;
}
