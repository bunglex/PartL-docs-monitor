def pick_pdf(start_url, selector, rule):
    html = fetch_html(start_url)
    soup = BeautifulSoup(html, "lxml")

    anchors = soup.select(selector) or soup.select("a[href$='.pdf']")

    def ok_includes_all(s, needles):
        return all(n.lower() in s.lower() for n in needles) if needles else True

    def bad_excludes_any(s, needles):
        return any(n.lower() in s.lower() for n in needles) if needles else False

    ranked = sorted(anchors, key=lambda a: score_anchor(a, rule), reverse=True)

    for a in ranked:
        href = a.get("href") or ""
        text = (a.get_text() or "")
        if not href.lower().endswith(".pdf"):
            continue

        # Hard rejections
        if bad_excludes_any(href, rule.get("url_excludes_any")):   continue
        if bad_excludes_any(text, rule.get("text_excludes_any")):  continue

        # Must satisfy ALL includes (URL and/or text)
        if not ok_includes_all(href, rule.get("url_includes_all")):   continue
        if not ok_includes_all(text, rule.get("text_includes_all")):  continue

        return abs_url(start_url, href), text.strip()

    return None, None
