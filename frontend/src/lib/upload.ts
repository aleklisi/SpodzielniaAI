/**
 * Attachment upload with progress (`fetch` cannot report upload progress, so XHR). Multipart
 * fields `file` and `keep_location` (backend 2.3). The CSRF header is added as for every
 * unsafe request.
 */

import { API_BASE, CSRF_COOKIE, CSRF_HEADER, readCookie } from "@/lib/api";
import type { components } from "@/lib/api-schema";
import { uploadError } from "@/lib/messages";

export type Attachment = components["schemas"]["AttachmentOut"];

export class UploadFailed extends Error {
  constructor(
    readonly status: number,
    readonly code: string | undefined,
  ) {
    super(uploadError(code, status));
    this.name = "UploadFailed";
  }
}

export interface UploadOptions {
  keepLocation: boolean;
  onProgress?: (fraction: number) => void;
  cookies?: () => string;
  xhr?: () => XMLHttpRequest;
}

export function uploadAttachment(noteId: string, file: File, options: UploadOptions): Promise<Attachment> {
  const form = new FormData();
  form.append("keep_location", options.keepLocation ? "true" : "false");
  form.append("file", file);
  const request = options.xhr?.() ?? new XMLHttpRequest();
  return new Promise((resolve, reject) => {
    request.open("POST", `${API_BASE}/notes/${encodeURIComponent(noteId)}/attachments`);
    request.withCredentials = true;
    const token = readCookie(CSRF_COOKIE, (options.cookies ?? (() => document.cookie))());
    if (token) request.setRequestHeader(CSRF_HEADER, token);
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) options.onProgress?.(event.loaded / event.total);
    };
    request.onerror = () => reject(new UploadFailed(0, undefined));
    request.onload = () => {
      let body: unknown = null;
      try {
        body = JSON.parse(request.responseText);
      } catch {
        body = null;
      }
      if (request.status === 202) {
        resolve(body as Attachment);
        return;
      }
      const detail = (body as { detail?: { error?: string } } | null)?.detail;
      reject(new UploadFailed(request.status, typeof detail === "object" ? detail?.error : undefined));
    };
    request.send(form);
  });
}
