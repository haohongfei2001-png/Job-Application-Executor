from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from urllib.parse import urlencode, urljoin, urlsplit

from playwright.sync_api import sync_playwright


@dataclass
class Candidate:
    title: str
    job_id: str
    location: str
    category: str
    job_url: str
    apply_url: str | None = None
    exact_title: bool = False
    campaign: str = ""
    employment_type: str = ""


SCHNEIDER_ALIASES = {"施耐德", "施耐德电气", "schneider", "schneider electric"}
OPPO_ALIASES = {"oppo", "oppo招聘", "oppo广东移动通信有限公司"}
OPPO_CAMPUS_ROOT = "https://careers.oppo.com/university/oppo/campus"
OPPO_CAMPUS_POST_LIST = OPPO_CAMPUS_ROOT + "/post"


def _is_oppo_company(company: str) -> bool:
    normalized = (company or "").strip().casefold()
    return normalized in OPPO_ALIASES or normalized.startswith("oppo")


def _norm_title(value: str) -> str:
    return re.sub(r"[\s·・_/（）()【】\[\]—–-]+", "", (value or "").casefold())


def _location_matches(requested: str, observed: str) -> bool:
    requested = (requested or "").strip().casefold().removesuffix("市")
    if requested in {"", "china", "中国"}:
        return True
    parts = re.split(r"[/|,，、]", (observed or "").casefold())
    return any(part.strip().removesuffix("市") == requested for part in parts)


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


def _public_connect():
    """Public discovery never attaches to the applicant's live Chrome profile."""
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=True)
    except Exception:
        pw.stop()
        raise
    ctx = browser.new_context()
    page = ctx.new_page()
    return pw, browser, ctx, page


def resolve_schneider(title: str, location: str = "China", max_pages: int = 8,
                      *, return_coverage: bool = False):
    pw, browser, ctx, page = _public_connect()
    out = []
    complete = False
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
        m = re.search(r"([\d,]+)\s*(?:Results|结果)", body, re.I)
        total = int(m.group(1).replace(",", "")) if m else None
        pages = min(max_pages, max(1, math.ceil((total or 10) / 10)))
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
        complete = total is not None and total <= max_pages * 10 and len(out) >= total
        return (out, complete) if return_coverage else out
    finally:
        try:
            page.close()
        except Exception:
            pass
        browser.close()
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


def resolve_oppo(title: str, location: str = "", max_pages: int = 40,
                 *, return_coverage: bool = False):
    """Query the observed public listing endpoint, proving every result page.

    This endpoint lists public jobs only. It never creates an application or
    attaches to the applicant's browser/profile.
    """
    pw, browser, ctx, page = _public_connect()
    aggregate: dict[str, Candidate] = {}
    complete = False
    try:
        page.goto(OPPO_CAMPUS_POST_LIST, wait_until="domcontentloaded", timeout=60000)
        endpoint = "https://careers.oppo.com/openapi/position/pageNew"
        expected_total = None
        expected_pages = None
        for number in range(1, max_pages + 1):
            payload = {
                "pageNum": number, "pageSize": 10, "positionName": title,
                "projectList": [], "positionTypeList": [],
                "workCityCodeList": [], "shareId": "",
            }
            response = ctx.request.post(
                endpoint, data=payload, headers={"Content-Type": "application/json"}, timeout=30000)
            if response.status != 200:
                break
            document = response.json()
            data = document.get("data") if isinstance(document, dict) and document.get("code") == 0 else None
            if not isinstance(data, dict) or not isinstance(data.get("records"), list):
                break
            total, pages, current = data.get("total"), data.get("pages"), data.get("current")
            if (not isinstance(total, int) or not isinstance(pages, int)
                    or current != number or total < 0 or pages < 0):
                break
            if expected_total is None:
                expected_total, expected_pages = total, pages
                if pages > max_pages:
                    break
            elif total != expected_total or pages != expected_pages:
                break
            valid_page = True
            for record in data["records"]:
                if not isinstance(record, dict):
                    valid_page = False
                    break
                job_id = str(record.get("idRecruitPosition") or "")
                name = str(record.get("positionName") or "").strip()
                if not job_id.isdigit() or not name or job_id in aggregate:
                    valid_page = False
                    break
                city = str(record.get("workCityName") or "")
                aggregate[job_id] = Candidate(
                    title=name, job_id=job_id, location=city,
                    category=str(record.get("positionTypeName") or ""),
                    job_url=f"{OPPO_CAMPUS_ROOT}/post/{job_id}",
                    exact_title=_norm_title(name) == _norm_title(title),
                    campaign=str(record.get("projectName") or ""),
                    employment_type=str(record.get("recruitmentTypeName") or ""),
                )
            if not valid_page:
                break
            if (total == 0 and pages == 0 and not data["records"] or
                    number == pages and len(aggregate) == total):
                complete = True
                break
            if number >= pages or not data["records"]:
                break
        if complete:
            for item in aggregate.values():
                if not item.exact_title:
                    continue
                try:
                    detail_response = ctx.request.get(
                        f"https://careers.oppo.com/openapi/position/detail?id={item.job_id}",
                        timeout=30000)
                    detail_body = detail_response.json() if detail_response.status == 200 else {}
                    detail = detail_body.get("data") if detail_body.get("code") == 0 else None
                    detail_cities = [
                        str(city.get("workCityName") or "")
                        for city in (detail.get("workCityVOList") or [])
                        if isinstance(city, dict)
                    ] if isinstance(detail, dict) else []
                    if (
                        not isinstance(detail, dict)
                        or str(detail.get("idRecruitPosition") or "") != item.job_id
                        or _norm_title(str(detail.get("positionName") or "")) != _norm_title(item.title)
                        or str(detail.get("projectName") or "") != item.campaign
                        or (item.location and item.location not in detail_cities)
                        or detail.get("positionStatus") != 0
                    ):
                        complete = False
                        break
                except Exception:
                    complete = False
                    break
        result = sorted(
            (item for item in aggregate.values() if _location_matches(location, item.location)),
            key=lambda item: (
                not item.exact_title,
                item.title.casefold(),
                item.location.casefold(),
                item.job_id,
            ),
        )
        return (result, complete) if return_coverage else result
    finally:
        try:
            page.close()
        except Exception:
            pass
        browser.close()
        pw.stop()


def resolve_known_landing(company: str, title: str, target_url: str) -> Candidate | None:
    if (
        _is_oppo_company(company)
        and is_oppo_campus_landing(target_url)
    ):
        items, complete = resolve_oppo(title, return_coverage=True)
        exact = [item for item in items if item.exact_title]
        return exact[0] if complete and len(exact) == 1 else None
    return None


def resolve(company: str, title: str, location: str = "China"):
    normalized = company.strip().casefold()
    if normalized in SCHNEIDER_ALIASES:
        return resolve_schneider(title, location)
    if _is_oppo_company(company):
        return resolve_oppo(title, location)
    raise NotImplementedError(f"company resolver not implemented yet: {company}")


def candidate_dicts(items):
    return [asdict(item) for item in items]
