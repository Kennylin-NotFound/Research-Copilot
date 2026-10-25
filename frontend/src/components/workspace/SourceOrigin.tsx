import type { Source } from './types';

export function SourceOrigin({ source, compact = false }: { source: Source; compact?: boolean }) {
  if (source.spec.kind !== 'web' || !source.spec.origin) return null;
  let url: URL;
  try { url = new URL(source.spec.origin); } catch { return null; }
  if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) return null;
  return <a className="source-origin" href={url.href} target="_blank" rel="noopener noreferrer" title={url.href}>
    {compact ? '打开原网页 ↗' : <>原网页：{url.href} ↗</>}
  </a>;
}
