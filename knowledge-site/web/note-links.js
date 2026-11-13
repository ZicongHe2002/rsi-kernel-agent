'use strict';

// Resolve note links against the document's category, not the web page URL.
window.NoteLinks = (() => {
  const invalid = (message = '文档链接无效，请检查路径。') => ({ error: message });
  const unsafePart = /[<>:"\\|?*\x00-\x1f\x7f]/;
  const reserved = /^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)/i;

  function decode(value) {
    const decoded = decodeURIComponent(value);
    // Decode once only. A second encoded layer must not become a path separator.
    if (/%[0-9a-f]{2}/i.test(decoded)) throw new Error('重复编码的文档链接无效。');
    return decoded;
  }

  function pathId(path, currentId, rooted) {
    if (path.startsWith('//') || path.includes('\\')) return invalid();
    const parts = rooted || path.startsWith('/') ? [] : String(currentId || '').split('/').slice(0, -1);
    for (const part of path.replace(/^\//, '').split('/')) {
      if (part === '.') continue;
      if (part === '..') {
        if (!parts.length) return invalid('文档链接超出了知识库目录。');
        parts.pop();
      } else {
        if (!part || part !== part.trim() || part.startsWith('.') || part.endsWith('.') || unsafePart.test(part) || reserved.test(part)) return invalid();
        parts.push(part);
      }
    }
    return { id: parts.join('/') };
  }

  function resolve(href, currentId, origin = window.location.origin) {
    if (typeof href !== 'string') return null;
    let raw = href.trim();
    if (!raw) return null;
    if (/[\\\x00-\x1f\x7f]/.test(raw)) return invalid();
    if (raw.startsWith('//')) return null;
    const scheme = raw.match(/^([a-z][a-z\d+.-]*):/i);
    if (scheme) {
      if (!/^https?$/i.test(scheme[1])) return null;
      try {
        const url = new URL(raw);
        if (url.origin !== origin || url.username || url.password) return null;
        // URL.pathname removes ../ before we can enforce the library boundary.
        const absolute = raw.match(/^https?:\/\/[^/?#]*([^?#]*)(.*)$/i);
        if (!absolute) return null;
        raw = (absolute[1] || '/') + absolute[2];
      } catch (_) { return null; }
    }
    try {
      const hashAt = raw.indexOf('#');
      const fragment = hashAt < 0 ? '' : decodeURIComponent(raw.slice(hashAt + 1));
      const beforeHash = hashAt < 0 ? raw : raw.slice(0, hashAt);
      if (!beforeHash) return currentId ? { id: currentId, fragment } : null;
      const queryAt = beforeHash.indexOf('?');
      const path = queryAt < 0 ? beforeHash : beforeHash.slice(0, queryAt);
      const query = queryAt < 0 ? '' : beforeHash.slice(queryAt + 1);
      // Only the site entry point is a ?note= route; /api/... remains an API URL.
      if (['', '/', '/index.html', 'index.html', './'].includes(path) && queryAt >= 0) {
        const values = query.split('&').map((part) => {
          const equal = part.indexOf('=');
          return [decodeURIComponent((equal < 0 ? part : part.slice(0, equal)).replace(/\+/g, ' ')), equal < 0 ? '' : part.slice(equal + 1)];
        }).filter(([key]) => key === 'note');
        if (!values.length) return null;
        if (values.length !== 1) return invalid();
        const id = decode(values[0][1].replace(/\+/g, ' '));
        if (id.startsWith('/') || !/\.md$/i.test(id)) return invalid();
        const result = pathId(id, '', true);
        return result.error ? result : { ...result, fragment };
      }
      const decoded = decode(path);
      if (!/\.md$/i.test(decoded)) return null;
      const result = pathId(decoded, currentId, false);
      return result.error ? result : { ...result, fragment };
    } catch (error) {
      return invalid(error.message === '重复编码的文档链接无效。' ? error.message : undefined);
    }
  }

  function headingSlug(text) {
    return String(text).trim().toLowerCase().replace(/[^\p{L}\p{N}\s_-]/gu, '').replace(/\s+/g, '-') || 'section';
  }

  return { resolve, headingSlug };
})();
