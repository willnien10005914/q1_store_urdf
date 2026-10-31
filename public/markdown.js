/** Minimal markdown → HTML for tutorial lessons. Escapes raw HTML; supports headings, lists, code, tables, links. */
const ESC = {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'};
const escape = (s) => String(s).replace(/[&<>"]/g, (c) => ESC[c]);

function inline(text) {
  return escape(text)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, '<em>$1</em>')
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
}

export function renderMarkdown(src) {
  const lines = String(src || '').replace(/\r\n?/g, '\n').split('\n');
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (/^```/.test(line)) {
      const lang = escape(line.slice(3).trim());
      const buf = [];
      i += 1;
      while (i < lines.length && !/^```/.test(lines[i])) { buf.push(lines[i]); i += 1; }
      i += 1;
      out.push(`<pre class="md-code"${lang ? ` data-lang="${lang}"` : ''}><code>${escape(buf.join('\n'))}</code></pre>`);
      continue;
    }
    if (/^\|/.test(line) && i + 1 < lines.length && /^\|?\s*[-:| ]+\|/.test(lines[i + 1])) {
      const rows = [];
      while (i < lines.length && /^\|/.test(lines[i])) {
        if (!/^\|?\s*[-:| ]+\|/.test(lines[i])) {
          rows.push(lines[i].replace(/^\||\|$/g, '').split('|').map((c) => c.trim()));
        }
        i += 1;
      }
      if (rows.length) {
        const head = rows.shift();
        out.push('<table class="md-table"><thead><tr>' + head.map((c) => `<th>${inline(c)}</th>`).join('') + '</tr></thead><tbody>' +
          rows.map((r) => '<tr>' + r.map((c) => `<td>${inline(c)}</td>`).join('') + '</tr>').join('') + '</tbody></table>');
      }
      continue;
    }
    if (/^#{1,3} /.test(line)) {
      const level = line.match(/^#+/)[0].length;
      out.push(`<h${level}>${inline(line.replace(/^#+\s+/, ''))}</h${level}>`);
      i += 1;
      continue;
    }
    if (/^>\s?/.test(line)) {
      const buf = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) { buf.push(lines[i].replace(/^>\s?/, '')); i += 1; }
      out.push(`<blockquote>${inline(buf.join(' '))}</blockquote>`);
      continue;
    }
    if (/^(\*|-|\d+\.) /.test(line)) {
      const ordered = /^\d+\. /.test(line);
      const items = [];
      while (i < lines.length && /^(\*|-|\d+\.) /.test(lines[i])) {
        items.push(`<li>${inline(lines[i].replace(/^(\*|-|\d+\.) /, ''))}</li>`);
        i += 1;
      }
      out.push(`<${ordered ? 'ol' : 'ul'}>${items.join('')}</${ordered ? 'ol' : 'ul'}>`);
      continue;
    }
    if (!line.trim()) { i += 1; continue; }
    const buf = [];
    while (i < lines.length && lines[i].trim() && !/^#{1,3} |^```|^\||^>\s?|^(\*|-|\d+\.) /.test(lines[i])) {
      buf.push(lines[i]);
      i += 1;
    }
    out.push(`<p>${inline(buf.join(' '))}</p>`);
  }
  return out.join('\n');
}
