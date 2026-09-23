from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from urllib.parse import urlencode, urljoin, urlsplit

from .browser import connect


@dataclass
class Candidate:
    title: str
    job_id: str
    location: str
    category: str
    job_url: str
    apply_url: str | None = None
    exact_title: bool = False


SCHNEIDER_ALIASES = {"施耐德", "施耐德电气", "schneider", "schneider electric"}
OPPO_ALIASES = {"oppo", "oppo招聘", "oppo广东移动通信有限公司"}
OPPO_CAMPUS_ROOT = "https://careers.oppo.com/university/oppo/campus"


def _norm_title(value: str) -> str:
    return re.sub(r"[\s·・_/（）()【】\[\]—–-]+", "", (value or "").casefold())


def is_oppo_campus_landing(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except Exception:
        return False
    return (
        (parsed.hostname or "").casefold() == "careers.oppo.com"
        and parsed.path.rstrip("/") == "/university/oppo/campus"
        and not parsed.fragment
    )


def _ancestor_text(anchor):
    return anchor.evaluate(
        """e=>{let p=e;for(let i=0;i<7&&p;i++,p=p.parentElement){const t=(p.innerText||'').trim();if((t.includes('Req ID:')||t.includes('申请ID'))&&(t.includes('Location')||t.includes('位置')))return t;}return (e.parentElement?.innerText||'').trim();}"""
    )


def _parse_card(title, href, text, apply_url, query_title):
    jid = ""
    loc = ""
    cat = ""
    m = re.search(r"(?:Req ID:|申请ID[:：]?)\s*(\d+)", text, re.I)
    jid = m.group(1) if m else ""
    m = re.search(r"(?:Location|位置)\s*\n([^\n]+)\n([^\n]+)", text, re.I)
    if m:
        loc = " / ".join(x.strip() for x in m.groups() if x.strip())
    m = re.search(r"(?:Categories|分类)\s*\n([^\n]+)", text, re.I)
    cat = m.group(1).strip() if m else ""
    return Candidate(
        title=title.strip(),
        job_id=jid,
        location=loc,
        category=cat,
        job_url=urljoin("https://careers.se.com", href),
        apply_url=apply_url,
        exact_title=title.strip().casefold() == query_title.strip().casefold(),
    )


def resolve_schneider(title: str, location: str = "China", max_pages: int = 8):
    pw, browser, ctx, page = connect()
    out = []
    try:
        base = "https://careers.se.com/jobs?" + urlencode(
            {
                "keywords": title,
                "location": location,
                "stretch": "10",
                "stretchUnit": "MILES",
                "sortBy": "relevance",
                "page": "1",
            }
        )
        page.goto(base, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2000)
        body = page.locator("body").inner_text()
        m = re.search(r"(\d+)\s*(?:Results|结果)", body, re.I)
        total = int(m.group(1)) if m else 10
        pages = min(max_pages, max(1, math.ceil(total / 10)))
        for n in range(1, pages + 1):
            if n > 1:
                page.goto(
                    re.sub(r"page=\d+", f"page={n}", base),
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                page.wait_for_timeout(1200)
            anchors = page.locator('a[href^="/jobs/"]')
            for i in range(anchors.count()):
                a = anchors.nth(i)
                txt = (a.inner_text() or "").strip()
                href = a.get_attribute("href") or ""
                if not txt or not re.match(r"^/jobs/\d+", href):
                    continue
                card = _ancestor_text(a)
                jidm = re.search(r"/jobs/(\d+)", href)
                jid = jidm.group(1) if jidm else ""
                apply = None
                if jid:
                    al = page.locator(f'a[href*="/jobs/{jid}/login"]')
                    if al.count():
                        apply = al.first.get_attribute("href")
                candidate = _parse_card(txt, href, card, apply, title)
                if candidate.job_id and all(x.job_id != candidate.job_id for x in out):
                    out.append(candidate)
        out.sort(
            key=lambda candidate: (
                not candidate.exact_title,
                candidate.title.casefold(),
                candidate.location.casefold(),
            )
        )
        return out
    finally:
        try:
            page.close()
        except Exception:
            pass
        pw.stop()


def _oppo_candidate_from_snapshot(href: str, text: str, query_title: str) -> Candidate | None:
    absolute = urljoin(OPPO_CAMPUS_ROOT + "/", href or "")
    match = re.search(r"/university/oppo/campus/post/(\d+)(?:[/?]|$)", absolute)
    if not match:
        return None
    job_id = match.group(1)
    lines = [" ".join(line.split()) for line in (text or "").splitlines() if line.strip()]
    query_norm = _norm_title(query_title)
    exact_line = next((line for line in lines if _norm_title(line) == query_norm), "")
    title = exact_line or (lines[0] if lines else "")
    if not title:
        return None
    location = next(
        (
            line
            for line in lines
            if line != title and re.search(r"(?:市|北京|上海|深圳|东莞|成都|杭州|南京|武汉|西安|重庆)", line)
        ),
        "",
    )
    category = next(
        (
            line
            for line in lines
            if line != title
            and line != location
            and re.search(r"(?:产品|软件|算法|硬件|设计|职能|销售|服务|工程|研究)类", line)
        ),
        "",
    )
    return Candidate(
        title=title,
        job_id=job_id,
        location=location,
        category=category,
        job_url=absolute,
        exact_title=bool(exact_line),
    )


def _collect_oppo_candidates(page, title: str) -> list[Candidate]:
    selector = (
        'a[href*="/university/oppo/campus/post/"], '
        'a[href*="/campus/post/"]'
    )
    anchors = page.locator(selector)
    out: list[Candidate] = []
    seen: set[str] = set()
    for index in range(anchors.count()):
        anchor = anchors.nth(index)
        try:
            href = anchor.get_attribute("href") or ""
            text = anchor.evaluate(
                """e=>{let p=e;for(let i=0;i<5&&p;i++,p=p.parentElement){const t=(p.innerText||'').trim();if(t.length>0&&t.length<1800)return t;}return (e.innerText||'').trim();}"""
            )
        except Exception:
            continue
        candidate = _oppo_candidate_from_snapshot(href, text, title)
        if candidate and candidate.job_id not in seen:
            seen.add(candidate.job_id)
            out.append(candidate)
    out.sort(
        key=lambda candidate: (
            not candidate.exact_title,
            candidate.title.casefold(),
            candidate.location.casefold(),
            candidate.job_id,
        )
    )
    return out


def _oppo_search_input(page):
    selector = (
        'input[placeholder*="搜索"], input[placeholder*="岗位"], '
        'input[placeholder*="职位"], input[placeholder*="search" i], '
        'input[type="search"]'
    )
    visible = []
    locator = page.locator(selector)
    for index in range(locator.count()):
        item = locator.nth(index)
        try:
            if item.is_visible() and item.is_enabled():
                visible.append(item)
        except Exception:
            continue
    return visible[0] if len(visible) == 1 else None


def resolve_oppo(title: str, location: str = "", max_scrolls: int = 6):
    pw, browser, ctx, attached = connect()
    page = ctx.new_page()
    try:
        page.goto(OPPO_CAMPUS_ROOT, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1500)
        candidates = _collect_oppo_candidates(page, title)
        exact = [item for item in candidates if item.exact_title]
        if exact:
            return exact + [item for item in candidates if not item.exact_title]

        search = _oppo_search_input(page)
        if search is not None:
            try:
                search.fill(title)
                search.press("Enter")
                page.wait_for_timeout(1200)
            except Exception:
                pass
            candidates = _collect_oppo_candidates(page, title)
            exact = [item for item in candidates if item.exact_title]
            if exact:
                return exact + [item for item in candidates if not item.exact_title]

        for _ in range(max_scrolls):
            try:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(700)
            except Exception:
                break
            candidates = _collect_oppo_candidates(page, title)
            exact = [item for item in candidates if item.exact_title]
            if exact:
                return exact + [item for item in candidates if not item.exact_title]
        return candidates
    finally:
        try:
            page.close()
        except Exception:
            pass
        pw.stop()


def resolve_known_landing(company: str, title: str, target_url: str) -> Candidate | None:
    if (
        company.strip().casefold() in OPPO_ALIASES
        and is_oppo_campus_landing(target_url)
    ):
        exact = [item for item in resolve_oppo(title) if item.exact_title]
        return exact[0] if len(exact) == 1 else None
    return None


def resolve(company: str, title: str, location: str = "China"):
    normalized = company.strip().casefold()
    if normalized in SCHNEIDER_ALIASES:
        return resolve_schneider(title, location)
    if normalized in OPPO_ALIASES:
        return resolve_oppo(title, location)
    raise NotImplementedError(f"company resolver not implemented yet: {company}")


def candidate_dicts(items):
    return [asdict(item) for item in items]
