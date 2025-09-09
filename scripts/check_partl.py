def find_pdf_links(html, pattern):
  import bs4
  try:
    soup = BS(html, "lxml")
  except bs4.FeatureNotFound:
    soup = BS(html, "html.parser")
  out = []
  for a in soup.select('a[href$=".pdf"]'):
    t = (a.get_text() or "").strip()
    h = a.get("href", "")
    if re.search(pattern, t, re.I) or re.search(pattern, h, re.I):
      out.append(h)
  return out
