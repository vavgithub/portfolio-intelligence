import os
import time
import re
from urllib.parse import urlparse, urljoin, unquote, parse_qs, urlencode, urlunparse
from playwright.sync_api import sync_playwright

from google.genai import types as genai_types


def _figma_design_url_to_embed_50(url: str) -> str:
    """Convert Figma design/file URL to embed URL with zoom=0.5 (50%). Embed API requires embed-host and supports zoom=0.5."""
    if not url or "figma.com" not in url or ("/design/" not in url and "/file/" not in url):
        return url
    parsed = urlparse(url)
    if "figma.com" not in parsed.netloc:
        return url
    new_netloc = "embed.figma.com"
    qs = parse_qs(parsed.query, keep_blank_values=True)
    qs["embed-host"] = ["portfolio-intelligence"]
    qs["zoom"] = ["0.5"]
    new_query = urlencode(qs, doseq=True)
    return urlunparse((parsed.scheme, new_netloc, parsed.path, parsed.params, new_query, parsed.fragment))


def _title_from_behance_url(url: str) -> str:
    """Extract project title from Behance gallery URL slug (e.g. .../gallery/123/Event-Booking-App -> 'Event Booking App')."""
    if not url or "behance.net" not in url:
        return ""
    m = re.search(r"/gallery/\d+/([^/?]+)", url)
    if not m:
        return ""
    slug = m.group(1).strip()
    if not slug:
        return ""
    title = unquote(slug).replace("-", " ").replace("_", " ")
    return title.strip() or ""

# Role → categories we keep for project filtering (lightweight title-based classification)
# Brand Designer includes Graphic and Illustration as fallback (many brand designers tag work that way on Behance)
ROLE_TO_CATEGORIES = {
    "ui ux": {"UI UX"},
    "ui/ux": {"UI UX"},
    "brand designer": {"Brand Identity", "Graphic", "Illustration"},
    "brand design": {"Brand Identity", "Graphic", "Illustration"},
    "motion designer": {"Motion"},
    "motion design": {"Motion"},
    "graphic designer": {"Graphic"},
    "graphic design": {"Graphic"},
}


def _classify_project_from_title_and_url(title: str, url: str) -> str:
    """Lightweight classification: UI UX, Brand Identity, Motion, Graphic, or Other. Uses title + URL keywords."""
    if not title:
        title = ""
    if not url:
        url = ""
    combined = f" {title.lower()} {url.lower()} "

    if any(k in combined for k in ("showreel", "reel", "motion", "animation", "after effects", "motion graphic", "kinetic")):
        return "Motion"
    if any(k in combined for k in ("brand", "logo", "identity", "visual identity", "branding")):
        return "Brand Identity"
    if any(k in combined for k in ("ui", "ux", "app ", "interface", "figma", "wireframe", "user flow", "case study")):
        return "UI UX"
    if any(k in combined for k in ("graphic", "poster", "print", "editorial", "typography", "layout")):
        return "Graphic"
    if any(k in combined for k in ("illustration", "digital painting", "character design", "drawing", "artwork", "campaign")):
        return "Illustration"
    return "Other"


def select_brand_projects_with_ai(projects, candidate_role, genai_client):
    """Use Gemini to pick the 3 most relevant Brand Identity projects from titles/URLs. Falls back to top 3 on failure."""
    if not projects or "brand" not in (candidate_role or "").lower() or not genai_client:
        return projects[:3] if projects else []

    # Hard bias before AI: prioritize clearly brand-identity projects and avoid pure UI/UX titles.
    positive_kw = (
        "brand", "branding", "identity", "logo", "visual identity", "packaging",
        "typography", "guideline", "guidelines", "campaign", "naming"
    )
    negative_kw = (
        "ui", "ux", "case study", "wireframe", "user flow", "dashboard",
        "app design", "website ui", "mobile app", "saas"
    )

    def brand_relevance(p):
        t = (p.get("title") or "").lower()
        u = (p.get("url") or "").lower()
        text = f"{t} {u}"
        pos = sum(2 for k in positive_kw if k in text)
        neg = sum(2 for k in negative_kw if k in text)
        # Keep some flexibility for mixed profiles but heavily penalize obvious UI/UX.
        return pos - neg

    ranked = sorted(projects, key=brand_relevance, reverse=True)
    narrowed = [p for p in ranked if brand_relevance(p) > 0]
    if len(narrowed) < 3:
        narrowed = ranked[: max(3, min(8, len(ranked)))]

    n = len(narrowed)
    print(f"  🎯 Asking Gemini to pick 3 Brand Identity projects from {n} narrowed options...")

    project_list = "\n".join(
        [f"{i+1}. {p.get('title', 'Untitled')} — {p.get('url', '')}" for i, p in enumerate(narrowed)]
    )
    prompt = f"""You are reviewing a Brand Identity designer's portfolio. Below are ALL their projects (number, title, URL).

{project_list}

Task: Choose exactly 3 projects that are BEST for evaluating Brand Identity design skills.

Brand Identity = logos, visual identity systems, packaging, brand campaigns, product branding, typography systems, brand guidelines, naming, brand strategy. Prefer these over: pure illustration, generic UI, personal art, or unrelated work.
IMPORTANT: Do NOT select pure UI/UX case studies unless no brand-relevant projects are available.

Reply with exactly one line: only three numbers separated by commas. Example: 2,5,7
Do not include any other text, explanation, or punctuation."""

    try:
        contents = [genai_types.Part.from_text(text=prompt)]
        response = genai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=contents,
        )
        text = (response.text or "").strip()
        # Parse 1-based numbers (e.g. "2, 5, 7" or "2,5,7" or "2. 5. 7")
        numbers = re.findall(r"\d+", text)
        indices_1based = [int(x) for x in numbers[:3]]
        indices_0based = [i - 1 for i in indices_1based if 1 <= i <= n]
        seen = set()
        selected = []
        for i in indices_0based:
            if i not in seen and 0 <= i < n:
                seen.add(i)
                selected.append(narrowed[i])
        if len(selected) < 3:
            for p in narrowed:
                if p not in selected and len(selected) < 3:
                    selected.append(p)
        # Final safeguard: if we still have obvious UI/UX picks and alternatives exist, replace them.
        if len(selected) == 3:
            ui_heavy = [p for p in selected if brand_relevance(p) < 0]
            if ui_heavy:
                alternatives = [p for p in ranked if p not in selected and brand_relevance(p) >= 0]
                for bad in ui_heavy:
                    if not alternatives:
                        break
                    replacement = alternatives.pop(0)
                    selected[selected.index(bad)] = replacement
        selected = selected[:3]
        titles = [s.get("title", "Untitled")[:50] for s in selected]
        print(f"  ✅ Gemini selected: {' | '.join(titles)}")
        return selected
    except Exception as e:
        print(f"  ⚠️ AI project selection failed ({e}), using first 3 projects.")
        return projects[:3]


def _filter_projects_for_role(projects: list, candidate_role: str | None):
    """
    Keep only projects matching candidate_role; take top 3 (newest first).
    If fewer than 2 matching, fall back to top 3 overall and set note.
    Returns (selected_3, note or None, reserve_list) — reserve is used to replace failed snapshots.
    """
    if not projects:
        return ([], None, [])
    top3 = projects[:3] if len(projects) > 3 else projects
    reserve = projects[3:] if len(projects) > 3 else []
    if not candidate_role:
        return (top3, None, reserve)

    role_lower = candidate_role.strip().lower()
    keep_categories = None
    for key, cats in ROLE_TO_CATEGORIES.items():
        if key in role_lower or role_lower in key:
            keep_categories = cats
            break
    if not keep_categories:
        return (top3, None, reserve)

    classified = []
    for p in projects:
        cat = _classify_project_from_title_and_url(p.get("title", ""), p.get("url", ""))
        classified.append((p, cat))

    matching = [p for p, cat in classified if cat in keep_categories]
    if len(matching) >= 2:
        return (matching[:3], None, matching[3:])
    # Fallback: top 3 overall (Behance order = newest first)
    return (top3, "limited role-matched work found", reserve)


class PortfolioBrowser:
    def __init__(self, base_snapshots_dir="/home/khyathi/portfolio_intelligence/snapshots"):
        self.base_snapshots_dir = base_snapshots_dir
        os.makedirs(self.base_snapshots_dir, exist_ok=True)
        self.current_snapshots_dir = self.base_snapshots_dir

    def identify_platform(self, url):
        domain = urlparse(url).netloc.lower()
        if "behance.net" in domain:
            return "behance"
        elif "dribbble.com" in domain:
            return "dribbble"
        elif "docs.google.com" in domain:
            return "google_docs"
        elif "drive.google.com" in domain and ("/drive/folders/" in url or "/file/" in url):
            return "google_drive"
        elif "framer.ai" in domain or "framer.com" in domain or "framer.website" in domain:
            return "framer"
        elif "figma.com" in domain:
            return "figma"
        return "personal"

    def extract_profile_metadata(self, page, platform):
        metadata = {
            "name": "Unknown",
            "title": "Unknown",
            "experience": "Not specified",
            "tools": [],
            "social_signals": {}
        }
        
        if platform == "behance":
            # Primary: New Behance BadgedDisplayName, Fallback: Legacy Profile-name
            name_el = page.query_selector(".BadgedDisplayName-root-qxP, .ProfileCard-badgedDisplayName-LwH, .Profile-name, .ProfileCard-name-F_p, h1")
            if name_el: metadata["name"] = name_el.inner_text().strip()
            
            title_el = page.query_selector(".Profile-occupation, [class*='ProfileCard-occupation'], .ProfileCard-occupation-S9p, ul[class*='ProfileDetails-userDetails'] li[class*='ProfileDetails-line']:first-child span")
            if title_el: metadata["title"] = title_el.inner_text().strip()
            
            stats = page.query_selector_all(".Profile-stats-item")
            for stat in stats:
                label_el = stat.query_selector(".Profile-stats-label")
                value_el = stat.query_selector(".Profile-stats-value")
                if label_el and value_el:
                    metadata["social_signals"][label_el.inner_text().strip()] = value_el.inner_text().strip()

        elif platform == "google_docs":
            # Google Docs title is often in the <title> or a specific header
            metadata["name"] = page.title().replace(" - Google Docs", "").strip() or "Google Doc Portfolio"
            metadata["title"] = "Document Portfolio Hub"

        elif platform == "google_drive":
            metadata["name"] = page.title().replace(" - Google Drive", "").strip() or "Drive Portfolio"
            metadata["title"] = "Google Drive folder"

        elif platform == "figma":
            metadata["name"] = page.title().replace(" - Figma", "").strip() or "Figma design"
            metadata["title"] = "Figma design file"

        elif platform == "dribbble":
            name_el = page.query_selector(".f-user-name")
            if name_el: metadata["name"] = name_el.inner_text().strip()
            title_el = page.query_selector(".f-user-bio")
            if title_el: metadata["title"] = title_el.inner_text().strip()
            
            stats = page.query_selector_all(".profile-stats-list-item")
            for stat in stats:
                text = stat.inner_text().strip()
                if "Followers" in text:
                    metadata["social_signals"]["Followers"] = text.split()[0]
                elif "Likes" in text:
                    metadata["social_signals"]["Likes"] = text.split()[0]

        else:
            metadata["name"] = page.title()
            h1 = page.query_selector("h1")
            if h1: metadata["name"] = h1.inner_text().strip()
            
            body_text = page.locator("body").inner_text()
            common_tools = ["Figma", "Adobe", "Illustrator", "Photoshop", "Sketch", "After Effects"]
            for tool in common_tools:
                if tool.lower() in body_text.lower():
                    metadata["tools"].append(tool)
            
            match = re.search(r"(\d+)\+? years", body_text, re.IGNORECASE)
            if match:
                metadata["experience"] = f"{match.group(1)}+ years"

        return metadata

    def extract_design_specs(self, page):
        """Extracts technical design specs: fonts, colors, and tech stack."""
        specs = {
            "fonts": [],
            "colors": [],
            "tech_stack": []
        }
        try:
            # Extract Fonts & Colors via JS for more accuracy
            result = page.evaluate('''() => {
                const styles = Array.from(document.querySelectorAll('*'))
                    .slice(0, 300) // Sample first 300 elements for speed
                    .map(el => {
                        const style = window.getComputedStyle(el);
                        return {
                            font: style.fontFamily.split(',')[0].replace(/['"]/g, ''),
                            color: style.color,
                            bg: style.backgroundColor
                        };
                    });

                const fonts = [...new Set(styles.map(s => s.font))].slice(0, 5);
                const colors = [...new Set(styles.map(s => s.bg))].filter(c => c !== 'transparent' && c !== 'rgba(0, 0, 0, 0)').slice(0, 5);
                
                return { fonts, colors };
            }''')
            specs["fonts"] = result["fonts"]
            specs["colors"] = result["colors"]
            
            # Detect Tech Stack via meta tags / headers
            content = page.content().lower()
            if "webflow" in content: specs["tech_stack"].append("Webflow")
            if "framer" in content: specs["tech_stack"].append("Framer")
            if "behance" in content: specs["tech_stack"].append("Behance")
            if "wp-content" in content: specs["tech_stack"].append("WordPress")
            if "next.js" in content or "_next" in content: specs["tech_stack"].append("Next.js")
            
        except Exception as e:
            print(f"  ⚠️ Design spec extraction failed: {e}")
            
        return specs

    def discover_projects(self, page, platform, candidate_role=None):
        url = page.url or ""
        if "behance.net" in url and "/gallery/" in url:
            title = _title_from_behance_url(url) or url.split("/")[-1].replace("-", " ").replace("_", " ").title() or "Project"
            return [{"url": url, "title": title}]

        projects = []
        seen_urls = set()
        
        if platform == "behance":
            # Scroll multiple times to trigger lazy loading
            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(0.8)
            
            # Wait for any project links to appear
            try:
                page.wait_for_selector('a[href*="/gallery/"]', timeout=10000)
            except Exception:
                pass

            # Grab all gallery links directly — works with current Behance HTML
            cards = page.query_selector_all('a[href*="/gallery/"]')
            
            for card in cards:
                href = card.get_attribute("href")
                if not href or "/gallery/" not in href:
                    continue
                full_url = href if href.startswith("http") else f"https://www.behance.net{href}"
                clean_url = full_url.split("?")[0].rstrip("/")
                
                # Skip non-project gallery links (category pages etc)
                parts = clean_url.split("/gallery/")
                if len(parts) < 2:
                    continue
                # Must have numeric ID after /gallery/
                gallery_id = parts[1].split("/")[0]
                if not gallery_id.isdigit():
                    continue
                    
                if clean_url in seen_urls:
                    continue
                    
                # Get title from aria-label, inner text, or URL slug
                title = (card.get_attribute("aria-label") or "").strip()
                if not title:
                    try:
                        title = card.inner_text().strip().split("\n")[0]
                    except Exception:
                        title = ""
                if not title or len(title) < 2:
                    title = _title_from_behance_url(full_url) or "Visual Project"
                    
                projects.append({"url": full_url, "title": title})
                seen_urls.add(clean_url)
                
                if len(projects) >= 8:
                    break
                
        elif platform == "dribbble":
            shots = page.query_selector_all("a.shot-thumbnail-link")
            for shot in shots[:5]:
                href = shot.get_attribute("href")
                full_url = f"https://dribbble.com{href}"
                if full_url in seen_urls: continue
                projects.append({"url": full_url, "title": "Dribbble Shot"})
                seen_urls.add(full_url)
                
        elif platform == "google_docs":
            print("  📄 Hub detected: Extracting links from Google Doc via export...")
            # Google Docs uses canvas rendering — Playwright can't read content in headless mode.
            # Use the public HTML export endpoint instead (works for public docs, no auth needed).
            try:
                import requests as _req
                import urllib.parse as _up
                from html.parser import HTMLParser

                # Extract the doc ID from the URL
                doc_id_match = re.search(r'/document/d/([a-zA-Z0-9_-]+)', page.url)
                if not doc_id_match:
                    raise ValueError("Could not extract Google Doc ID from URL")
                doc_id = doc_id_match.group(1)

                # Fetch title from plain text
                txt_resp = _req.get(f"https://docs.google.com/document/d/{doc_id}/export?format=txt", timeout=20)
                doc_lines = [l.strip() for l in txt_resp.text.splitlines() if l.strip()]

                # Fetch hyperlinks from HTML export
                html_resp = _req.get(f"https://docs.google.com/document/d/{doc_id}/export?format=html", timeout=20)
                raw_hrefs = re.findall(r'href=["\']([^"\']+)["\']', html_resp.text)
                
                for href in raw_hrefs:
                    # Unwrap google.com/url?q= wrapper
                    if "google.com/url" in href:
                        qs = _up.parse_qs(_up.urlparse(href).query)
                        href = qs.get("q", [href])[0]
                    # Decode HTML entities (&amp; etc)
                    href = href.replace("&amp;", "&").split("&sa=")[0].rstrip(".,;")
                    
                    is_project = any(k in href for k in [
                        "figma.com", "behance.net", "dribbble.com",
                        "framer.ai", "framer.site", "webflow.io", "notion.so"
                    ])
                    if is_project and href not in seen_urls:
                        # Match this link to the closest line in the doc text as the title
                        title = doc_lines[len(projects)] if len(projects) < len(doc_lines) else "Linked Project"
                        projects.append({"url": href, "title": title})
                        seen_urls.add(href)
                    if len(projects) >= 15:
                        break
            except Exception as e:
                print(f"  ⚠️ Google Doc link extraction failed: {e}")
            
            print(f"  📎 Found {len(projects)} project links in the doc.")

        elif platform == "google_drive":
            try:
                page.wait_for_selector('a[href*="/file/d/"], [data-id], div[role="row"]', timeout=8000)
            except Exception:
                pass
            time.sleep(2)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(2)
            file_entries = page.evaluate("""() => {
                const items = [];
                const seen = new Set();
                const re = /\\/file\\/d\\/([a-zA-Z0-9_-]+)/;
                const reId = /^[a-zA-Z0-9_-]{20,}$/;
                function add(id, name) {
                    if (!id || seen.has(id)) return;
                    seen.add(id);
                    items.push({ id, name: (name || 'Project').trim().slice(0, 200), href: 'https://drive.google.com/file/d/' + id + '/view' });
                }
                document.querySelectorAll('a[href*="/file/d/"], a[href*="drive.google.com/file"]').forEach(a => {
                    const href = a.getAttribute('href') || '';
                    const m = href.match(re);
                    if (m) add(m[1], a.innerText || a.getAttribute('aria-label') || '');
                });
                document.querySelectorAll('[data-id]').forEach(el => {
                    const id = (el.getAttribute('data-id') || '').trim();
                    if (reId.test(id)) add(id, el.getAttribute('data-name') || el.innerText || '');
                });
                document.querySelectorAll('div[role="row"] a[href], [role="listbox"] a[href]').forEach(a => {
                    const href = a.getAttribute('href') || '';
                    const m = href.match(re);
                    if (m) add(m[1], a.innerText || a.getAttribute('aria-label') || '');
                });
                return items.slice(0, 12);
            }""")
            for entry in (file_entries or []):
                view_url = entry.get("href") or ("https://drive.google.com/file/d/" + entry.get("id", "") + "/view")
                title = (entry.get("name") or "Project").strip() or "Drive file"
                if view_url not in seen_urls:
                    projects.append({"url": view_url, "title": title})
                    seen_urls.add(view_url)
            if not projects:
                body_text = page.locator("body").inner_text()
                if "Sign in" in body_text or "sign in" in body_text.lower():
                    print("  ⚠️ Google Drive folder requires login — no file list; using folder URL as single project.")
                projects.append({"url": page.url, "title": page.title() or "Drive folder"})

        elif platform == "figma":
            time.sleep(3)
            title = page.title().replace(" - Figma", "").strip() or "Figma design"
            projects.append({"url": page.url, "title": title})

        elif platform == "framer":
            # Framer: find "FEATURED CASES" (or Cases / Work / Projects) section, get links only from that section
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(2)
            base_url = page.url
            base_netloc = urlparse(base_url).netloc
            # Get links from the Featured Cases / Work / Projects section (Behance-style card grid)
            section_links = page.evaluate("""() => {
                const headingRegex = /featured\\s*cases|featured cases|cases|\\bwork\\b|projects|portfolio|my work/i;
                let sectionHeading = null;
                for (const h of document.querySelectorAll('h1, h2, h3, h4, h5, h6, [class*="title"], [class*="Title"]')) {
                    const t = (h.textContent || '').trim();
                    if (headingRegex.test(t)) {
                        sectionHeading = h;
                        break;
                    }
                }
                if (!sectionHeading) return [];

                function getLinks(container) {
                    if (!container || container === document.body) return [];
                    const links = container.querySelectorAll('a[href]');
                    return Array.from(links).slice(0, 25).map(a => ({ href: a.getAttribute('href'), text: (a.innerText || '').trim().slice(0, 300) }));
                }

                const candidates = [
                    sectionHeading.closest('section'),
                    sectionHeading.closest('[class*="Section"]'),
                    sectionHeading.closest('[class*="section"]'),
                    sectionHeading.parentElement,
                    sectionHeading.parentElement && sectionHeading.parentElement.parentElement,
                    sectionHeading.parentElement && sectionHeading.parentElement.parentElement && sectionHeading.parentElement.parentElement.parentElement,
                    sectionHeading.nextElementSibling,
                    sectionHeading.parentElement && sectionHeading.parentElement.nextElementSibling,
                    sectionHeading.parentElement && sectionHeading.parentElement.parentElement && sectionHeading.parentElement.parentElement.nextElementSibling
                ].filter(Boolean);

                for (const container of candidates) {
                    const links = getLinks(container);
                    if (links.length >= 2) return links;
                }

                let el = sectionHeading.parentElement;
                for (let i = 0; i < 10 && el && el !== document.body; i++) {
                    const links = getLinks(el);
                    if (links.length >= 2) return links;
                    el = el.parentElement;
                }

                return [];
            }""")

            if not section_links:
                section_links = page.evaluate("""() => {
                    const base = window.location.origin + '/';
                    return Array.from(document.querySelectorAll('a[href]'))
                        .filter(a => {
                            const href = (a.getAttribute('href') || '').trim();
                            return href && !href.startsWith('#') && !href.startsWith('javascript:') && (href.startsWith('/') || href.startsWith(base));
                        })
                        .slice(0, 50)
                        .map(a => ({ href: a.getAttribute('href'), text: (a.innerText || '').trim().slice(0, 300) }));
                }""")

            # Paths/slugs that are nav or non-project pages — never count as projects
            skip_slugs = {"about", "about-me", "contact", "privacy", "terms", "home", "blog", "services", "resume", "pricing", "hire", "faq", "experience", "linkedin", "twitter", "instagram", "dribbble", "behance", "email", "cv", "canvas", "my-canvas", "my_canvas", "menu", "work"}
            # When role is UI/UX, prefer projects whose title/URL suggest UI/UX
            role_ui_ux = candidate_role and "ui" in candidate_role.lower() and "ux" in candidate_role.lower()
            ui_ux_keywords = ("ui", "ux", "case study", "app", "web design", "website", "digital", "product design", "interface")
            link_items = []
            if section_links:
                for item in section_links:
                    href = (item or {}).get("href") or ""
                    content = ((item or {}).get("text") or "").strip()
                    if not href or href.startswith("#") or href.startswith("javascript:"):
                        continue
                    full_url = urljoin(base_url, href).split("?")[0].rstrip("/")
                    parsed = urlparse(full_url)
                    if parsed.netloc != base_netloc:
                        continue
                    path = parsed.path.strip("/")
                    if not path:
                        continue
                    segments = path.split("/")
                    first_slug = (segments[0] or "").lower()
                    if first_slug in skip_slugs or first_slug.startswith("about"):
                        continue
                    content_lower = (content or "").lower().strip()
                    if content_lower in {"about", "about me", "contact", "home", "services", "resume", "hire me", "get in touch", "my canvas"} and len(segments) <= 1:
                        continue
                    if content and any(k in content_lower for k in ["download resume", "contact now", "email me", "menu", "let's work together", "about me", "about us"]):
                        continue
                    if content_lower.startswith("about ") or content_lower == "about" or content_lower == "my canvas":
                        continue
                    # Links from Featured Cases section are trusted as projects; only skip obvious nav
                    path_lower = path.lower()
                    title = content if len(content) > 2 else path.split("/")[-1].replace("-", " ").title()
                    is_ui_ux = any(k in (title + " " + path_lower).lower() for k in ui_ux_keywords)
                    link_items.append({"url": full_url, "title": title, "is_ui_ux": is_ui_ux})
            if link_items:
                # Dedupe by URL, prefer UI/UX when candidate_role is UI UX
                seen = set()
                ordered = sorted(link_items, key=lambda x: (not x.get("is_ui_ux", False), x["url"]))
                # Framer: homepage = portfolio (full page scroll); prepend it so we always have it, then sub-pages
                homepage_entry = {"url": base_url, "title": page.title() or "Portfolio"}
                projects.append(homepage_entry)
                for item in ordered:
                    if item["url"] in seen or item["url"].rstrip("/") == base_url.rstrip("/") or len(projects) >= 9:
                        continue
                    seen.add(item["url"])
                    projects.append({"url": item["url"], "title": item["title"]})
            # If section had no valid links, fall back to scanning all links
            if not projects:
                links = page.query_selector_all("a[href]")
                for link in links:
                    href = link.get_attribute("href")
                    if not href or href.startswith("#") or href.startswith("javascript:"):
                        continue
                    full_url = urljoin(base_url, href).split("?")[0].rstrip("/")
                    parsed = urlparse(full_url)
                    if parsed.netloc != base_netloc:
                        continue
                    path = parsed.path.strip("/")
                    if not path:
                        continue
                    segments = path.split("/")
                    first_slug = (segments[0] or "").lower()
                    if first_slug in skip_slugs or first_slug.startswith("about"):
                        continue
                    content = link.inner_text().strip()
                    content_lower = (content or "").lower()
                    if content_lower in {"about", "about me", "contact", "home", "services", "resume", "hire me", "get in touch", "my canvas"} and len(segments) <= 1:
                        continue
                    if content and any(k in content_lower for k in ["download resume", "contact now", "email me", "menu", "let's work together", "about me", "about us"]):
                        continue
                    if content_lower.startswith("about ") or content_lower == "about" or content_lower == "my canvas":
                        continue
                    path_lower = path.lower()
                    is_project_path = any(k in path_lower for k in ["work", "project", "case", "portfolio", "design", "app", "ui", "ux", "brand"])
                    has_image = link.query_selector("img") and not (link.query_selector("img[class*='logo']") or link.query_selector("img[src*='logo']"))
                    long_descriptive = len(content) > 20 and not any(k in content.lower() for k in ["copyright", "rights reserved", "terms", "privacy"])
                    content_looks_project = content and any(k in content.lower() for k in ["case study", "ui/ux", "brand", "identity", "e-commerce", "web design", "pitch deck", "animation"])
                    multi_segment = len(segments) >= 2
                    single_slug_project = len(segments) == 1 and (has_image or len(content or "") > 15 or content_looks_project)
                    if not (is_project_path or has_image or long_descriptive or multi_segment or single_slug_project):
                        continue
                    if full_url in seen_urls:
                        continue
                    title = content if len(content) > 2 else path.split("/")[-1].replace("-", " ").title()
                    projects.append({"url": full_url, "title": title})
                    seen_urls.add(full_url)
                    if len(projects) >= 8:
                        break
            if not projects:
                projects.append({"url": base_url, "title": page.title() or "Portfolio"})
            elif platform == "framer":
                base_norm = base_url.rstrip("/")
                if not any((p.get("url") or "").rstrip("/") == base_norm for p in projects):
                    projects.insert(0, {"url": base_url, "title": page.title() or "Portfolio"})

        else:
            # Personal Portfolios: Scroll to ensure all content is loaded
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(2)
            
            links = page.query_selector_all("a")
            # Heuristic: Sort links by path depth to find actual project pages vs nav hubs
            for link in links:
                content = link.inner_text().strip()
                href = link.get_attribute("href")
                if not href or href.startswith("#") or "javascript:" in href:
                    continue
                
                # Robust URL joining
                full_url = urljoin(page.url, href)
                parsed = urlparse(full_url)
                
                # Only allow internal links for personal portfolios
                if parsed.netloc != urlparse(page.url).netloc:
                    continue
                
                path = parsed.path.rstrip('/')
                
                # Skip homepage, logo links, and exact standard nav matches
                nav_paths = ["", "/", "/work", "/about", "/contact", "/search", "/home"]
                if path.lower() in nav_paths:
                    continue
                
                # Check for project markers
                # 1. Keywords in text
                is_likely_project = any(k in content.lower() for k in ["view", "project", "case study", "work", "portfolio"])
                
                # 2. Keywords in URL
                if any(k in path.lower() for k in ["/project/", "/work/", "/case-study/", "/p/"]):
                    is_likely_project = True
                
                # 3. Contains an image (very common for project cards)
                img = link.query_selector("img")
                if img:
                    img_class = img.get_attribute("class") or ""
                    img_src = img.get_attribute("src") or ""
                    # Exclude sites logos (usually small or have 'logo' in class/src)
                    if "logo" in img_class.lower() or "logo" in img_src.lower():
                        pass 
                    else:
                        is_likely_project = True
                
                # 4. Long descriptive text (Framer case)
                if len(content) > 15 and not any(k in content.lower() for k in ["copyright", "rights reserved", "terms", "privacy"]):
                    # If it's a long link text on a portfolio home, it's often a project title
                    is_likely_project = True

                if is_likely_project:
                    clean_url = full_url.split('?')[0].rstrip('/')
                    if clean_url not in seen_urls:
                        # Fallback title: use link text or path
                        title = content if len(content) > 3 else path.split('/')[-1].replace('-', ' ').title()
                        projects.append({"url": full_url, "title": title})
                        seen_urls.add(clean_url)
                
                if len(projects) >= 6:
                    break
        return projects

    def _figma_zoom_to_100_via_ui(self, page):
        """Set Figma canvas zoom to 100% via UI (browser steals Ctrl+1 for tab switch)."""
        try:
            # Click the zoom readout (e.g. "4%" in top-right) to open dropdown — use regex to match any zoom %
            zoom_btn = page.get_by_text(re.compile(r"^\d+%$"), exact=True).first
            zoom_btn.click(timeout=4000)
            time.sleep(0.6)
            # Click the "100%" option in the dropdown
            page.get_by_text("100%", exact=True).first.click(timeout=4000)
            return
        except Exception:
            pass
        try:
            # Fallback: any element with just a number and % (zoom readout)
            page.locator('[class*="zoom"]:visible, [data-testid*="zoom"]').first.click(timeout=3000)
            time.sleep(0.6)
            page.get_by_text("100%").first.click(timeout=3000)
            return
        except Exception:
            pass
        # JS fallback: find in top-right quadrant element with text like "4%", click, then click "100%"
        try:
            page.evaluate("""() => {
                const candidates = document.querySelectorAll('button, [role="button"], [class*="zoom"], [class*="Zoom"], span, div');
                const vw = window.innerWidth;
                for (const el of candidates) {
                    const t = (el.textContent || '').trim();
                    if (!/^\\d+%$/.test(t)) continue;
                    const r = el.getBoundingClientRect();
                    if (r.right < vw * 0.5) continue;
                    el.click();
                    return true;
                }
                return false;
            }""")
            time.sleep(0.7)
            page.evaluate("""() => {
                const options = document.querySelectorAll('[role="menuitem"], [role="option"], [role="listbox"] *, [class*="menu"] *, li');
                for (const el of options) {
                    if (/^100\\s*%$/.test((el.textContent || '').trim())) {
                        el.click();
                        return true;
                    }
                }
                return false;
            }""")
        except Exception:
            pass

    def _figma_set_zoom_50_via_dropdown(self, page, vw, vh):
        """Open zoom dropdown, set 50% (fallback 100%). Use JS to find real zoom control and menu item."""
        # 1. Click canvas so Figma top bar is active, then click zoom readout (top-right, small element showing "4%" etc.)
        page.mouse.click(vw // 2, vh // 2)
        time.sleep(0.3)
        # 2. Open dropdown: JS find clickable in top-right with text like "4%" (zoom readout), else fallback position
        opened = page.evaluate("""() => {
            const topRight = document.elementsFromPoint(window.innerWidth - 100, 50);
            for (const el of topRight) {
                const t = (el.textContent || '').trim();
                if (/^\\d+%$/.test(t) && el.getBoundingClientRect().width < 80) {
                    el.click();
                    return true;
                }
            }
            const all = document.querySelectorAll('button, [role="button"], [class*="zoom"], [class*="Zoom"]');
            for (const el of all) {
                const t = (el.textContent || '').trim();
                if (/^\\d+%$/.test(t)) {
                    el.click();
                    return true;
                }
            }
            return false;
        }""")
        if not opened:
            page.mouse.click(vw - 80, 55)
        time.sleep(1.2)
        # 3. Click "50%" in the dropdown (element in top half of screen, not canvas)
        def click_zoom_option(pct):
            return page.evaluate("""(pct) => {
                const candidates = document.querySelectorAll('*');
                for (const el of candidates) {
                    const t = (el.textContent || '').trim();
                    if (t !== pct) continue;
                    const r = el.getBoundingClientRect();
                    if (r.width === 0 || r.height === 0) continue;
                    if (r.top > 450 || r.bottom < 0) continue;
                    if (r.top < 20) continue;
                    el.click();
                    return true;
                }
                return false;
            }""", pct)
        clicked_50 = click_zoom_option("50%")
        if not clicked_50:
            try:
                page.get_by_role("menuitem", name="50%").first.click(timeout=1500)
                clicked_50 = True
            except Exception:
                try:
                    page.locator('[role="menu"] >> text=50%').first.click(timeout=1500)
                    clicked_50 = True
                except Exception:
                    page.mouse.click(vw - 80, 180)
        if not clicked_50:
            click_zoom_option("100%")
        time.sleep(1.0)

    def _figma_wait_for_zoom_applied(self, page, max_wait_sec=10):
        """Poll until zoom readout shows 50% or 100% (so we don't screenshot before zoom applies)."""
        for _ in range(max_wait_sec * 2):
            try:
                visible = page.evaluate("""() => {
                    const targets = document.querySelectorAll('*');
                    for (const el of targets) {
                        const t = (el.textContent || '').trim();
                        if (t === '50%' || t === '100%') {
                            const r = el.getBoundingClientRect();
                            if (r.width > 0 && r.height > 0 && r.top < 300) return true;
                        }
                    }
                    return false;
                }""")
                if visible:
                    return
            except Exception:
                pass
            time.sleep(0.5)

    def _figma_capture_2d_grid(self, page, filename_prefix, part_idx_start, screenshots, vw, vh, max_shots=9):
        """Capture Figma 2D canvas by scrolling left-right and top-bottom in a grid (not just vertical)."""
        # Get scrollable dimensions — try document and any inner scroll container (Figma canvas)
        dims = page.evaluate("""() => {
            const body = document.body;
            const docEl = document.documentElement;
            let w = Math.max(body.scrollWidth || 0, docEl.scrollWidth || 0, body.clientWidth || 0);
            let h = Math.max(body.scrollHeight || 0, docEl.scrollHeight || 0, body.clientHeight || 0);
            document.querySelectorAll('[class*="canvas"], [class*="Canvas"], [class*="scroll"]').forEach(el => {
                if (el.scrollWidth > w) w = el.scrollWidth;
                if (el.scrollHeight > h) h = el.scrollHeight;
            });
            return { scrollWidth: w, scrollHeight: h };
        }""")
        total_w = dims.get("scrollWidth", vw)
        total_h = dims.get("scrollHeight", vh)
        max_x = max(0, total_w - vw)
        max_y = max(0, total_h - vh)
        # Build 2D grid: left/center/right × top/middle/bottom (3×3 = 9), cap at max_shots
        if max_x <= 0 and max_y <= 0:
            positions_2d = [(0, 0)]
        else:
            x_vals = [0, max_x // 2, max_x] if max_x > 0 else [0]
            y_vals = [0, max_y // 2, max_y] if max_y > 0 else [0]
            positions_2d = [(x, y) for x in x_vals for y in y_vals][:max_shots]
        part_idx = part_idx_start
        for (scroll_x, scroll_y) in positions_2d:
            # Try all possible scroll targets (Figma may use window, body, or inner canvas div)
            page.evaluate("""([x, y]) => {
                window.scrollTo(x, y);
                document.documentElement.scrollLeft = x;
                document.documentElement.scrollTop = y;
                document.body.scrollLeft = x;
                document.body.scrollTop = y;
                document.querySelectorAll('[class*="canvas"], [class*="Canvas"], [class*="scroll"]').forEach(el => {
                    if (el.scrollWidth > el.clientWidth || el.scrollHeight > el.clientHeight) {
                        el.scrollLeft = x;
                        el.scrollTop = y;
                    }
                });
            }""", [scroll_x, scroll_y])
            time.sleep(0.5)
            path = os.path.join(self.current_snapshots_dir, f"{filename_prefix}_part_{part_idx}.png")
            page.screenshot(path=path, full_page=False)
            screenshots.append(path)
            part_idx += 1
        return part_idx

    def snapshot_project(self, context, url, filename_prefix, capture_section_only=False, candidate_role=None, existing_page=None):
        try:
            screenshots = []
            case_study_text = ""
            if existing_page is not None:
                page = existing_page
                print(f"  📸 Snapshotting (reusing open page): {url[:80]}...")
            else:
                page = context.new_page()
                print(f"  📸 Snapshotting: {url}")

            try:
                if "figma.com" in url and ("/proto/" in url or "/deck/" in url) and "hide-ui=1" not in url:
                    url = url + ("&" if "?" in url else "?") + "hide-ui=1"
                # Use lighter wait for URLs that never reach networkidle (Figma, Google Docs, Drive)
                is_heavy = any(k in url for k in ["figma.com", "docs.google.com", "drive.google.com"])
                if existing_page is None:
                    if "behance.net" in url:
                        page.goto(url, wait_until="domcontentloaded", timeout=30000)
                        time.sleep(1.5)
                    else:
                        wait_until = "domcontentloaded" if is_heavy else "networkidle"
                        page.goto(url, wait_until=wait_until, timeout=30000)
                        if is_heavy:
                            time.sleep(3)  # Give canvas-based apps time to render
                elif is_heavy:
                    time.sleep(1)  # Page already open; brief settle

                # Figma /design/, /file/, or embed: use zoom dropdown then 2D grid (persistent Chrome context)
                is_figma_design_file = (
                    ("figma.com" in url and ("/design/" in url or "/file/" in url))
                    or "embed.figma.com" in url
                )
                if is_figma_design_file:
                    vw = page.viewport_size.get("width", 1440)
                    vh = page.viewport_size.get("height", 900)
                    print("  ⏳ Setting zoom to 50% (dropdown)...")
                    self._figma_set_zoom_50_via_dropdown(page, vw, vh)
                    print("  ⏳ Waiting for zoom to apply...")
                    self._figma_wait_for_zoom_applied(page, max_wait_sec=10)
                    time.sleep(1.0)
                    print("  📸 Capturing 2D grid (top to bottom, left to right).")
                    self._figma_capture_2d_grid(page, filename_prefix, 0, screenshots, vw, vh, max_shots=9)
                    try:
                        main_content = page.query_selector("main, article, [class*='canvas']")
                        case_study_text = main_content.inner_text().strip()[:5000] if main_content else ""
                    except Exception:
                        case_study_text = ""
                else:
                    total_height = page.evaluate("document.body.scrollHeight")
                    viewport_height = page.viewport_size["height"]
                    max_scroll = max(0, total_height - viewport_height)

                    # When we're snapshotting the whole portfolio as one "project", capture only from "FEATURED CASES" onward (skip About me, My Canvas, etc.)
                    section_top = 0
                    section_height = total_height
                    if capture_section_only and candidate_role:
                        section_bounds = page.evaluate("""() => {
                            const headings = Array.from(document.querySelectorAll('h1, h2, h3, h4, h5, h6, [class*="title"], [class*="Title"]'));
                            const featuredHeading = headings.find(h => {
                                const t = (h.textContent || '').trim();
                                return /featured\\s*cases|featured cases/i.test(t);
                            });
                            if (!featuredHeading) return null;
                            const startY = featuredHeading.getBoundingClientRect().top + window.scrollY;
                            let endY = startY + 6000;
                            const afterHeadings = headings.filter(h => {
                                const y = h.getBoundingClientRect().top + window.scrollY;
                                return y > startY + 100;
                            });
                            const stopTitle = afterHeadings.find(h => /services|let'?s?\\s*work|together|contact/i.test((h.textContent || '').trim()));
                            if (stopTitle) {
                                const stopY = stopTitle.getBoundingClientRect().top + window.scrollY;
                                if (stopY > startY) endY = Math.min(endY, stopY);
                            }
                            return { top: startY, height: Math.max(400, endY - startY) };
                        }""")
                        if section_bounds and isinstance(section_bounds, dict) and section_bounds.get("height", 0) >= 200:
                            section_top = max(0, int(section_bounds["top"]))
                            section_height = max(viewport_height, int(section_bounds["height"]))
                            print(f"  📌 Capturing from FEATURED CASES only (scroll {section_top}–{section_top + section_height}px)")

                    # Extract text for OCR/Context (from full page or section)
                    main_content = page.query_selector("main, article, .project-canvas, #project-content, #main-content")
                    if main_content:
                        case_study_text = main_content.inner_text().strip()
                    else:
                        case_study_text = page.locator("body").inner_text().strip()
                    case_study_text = case_study_text[:5000]

                    # Behance lazy-load: scroll once to bottom (and back) so images load before we capture
                    if "behance.net" in url:
                        for scroll_y in [0, max_scroll, 0]:
                            page.evaluate(f"window.scrollTo(0, {scroll_y})")
                            time.sleep(0.5)
                        time.sleep(1.0)

                    # Disable CSS animations/transitions for deterministic screenshots
                    page.add_style_tag(content="* { animation: none !important; transition: none !important; }")

                    # Scroll range: full page or only the section (when capture_section_only)
                    scroll_end = section_top + section_height
                    scroll_end = min(scroll_end, total_height)
                    range_height = scroll_end - section_top
                    if range_height < viewport_height:
                        positions = [section_top]
                    else:
                        num_shots = 5 if range_height < 4000 else (8 if range_height <= 8000 else 12)
                        num_shots = min(num_shots, max(1, (range_height + viewport_height - 1) // viewport_height))
                        step = max(viewport_height, (range_height - viewport_height) // max(1, num_shots - 1))
                        positions = [section_top + i * step for i in range(num_shots)]
                        positions[-1] = min(max_scroll, scroll_end - viewport_height) if scroll_end - section_top > viewport_height else positions[-1]

                    # Timeout for image loading so one slow/broken image doesn't hang the run
                    IMAGE_WAIT_TIMEOUT_MS = 5000

                    for i, scroll_y in enumerate(positions):
                        page.evaluate(f"window.scrollTo(0, {scroll_y})")
                        if not is_heavy:
                            try:
                                page.wait_for_load_state("networkidle", timeout=2000)
                            except Exception:
                                pass
                        page.evaluate(f"""() => {{
                            const images = document.querySelectorAll('img');
                            const incomplete = Array.from(images).filter(img => !img.complete);
                            const loaded = Promise.all(incomplete.map(img =>
                                new Promise(resolve => {{ img.onload = img.onerror = resolve; }})));
                            const timeout = new Promise(resolve => setTimeout(resolve, {IMAGE_WAIT_TIMEOUT_MS}));
                            return Promise.race([loaded, timeout]);
                        }}""")
                        time.sleep(0.7)
                        path = os.path.join(self.current_snapshots_dir, f"{filename_prefix}_part_{i}.png")
                        page.screenshot(path=path, full_page=False)
                        screenshots.append(path)
            finally:
                try:
                    if existing_page is None and page is not None:
                        page.close()
                except Exception:
                    pass

            return screenshots, case_study_text
        except Exception as e:
            print(f"  ⚠️ Snapshot failed for {url}: {e}")
            return [], ""

    def full_pipeline_scan(self, url, run_id="", candidate_role=None, genai_client=None):
        platform = self.identify_platform(url)
        if platform == "google_drive":
            metadata = {
                "name": "Google Drive (cannot evaluate)",
                "url": url,
                "platform": "google_drive",
                "skipped": True,
                "skip_reason": "Google Drive requires authentication — route to human review",
            }
            folder_name = f"drive_skipped_{run_id}" if run_id else "drive_skipped"
            print("  ⏭️ Skipping: Google Drive requires authentication — route to human review.")
            return (metadata, [], folder_name)
        load_url = url
        if platform == "figma" and ("/proto/" in url or "/deck/" in url):
            if "hide-ui=1" not in url:
                load_url = url + ("&" if "?" in url else "?") + "hide-ui=1"
            print("  📐 Figma proto/deck: loading with hide-ui=1 (design only, no chrome)")
        use_persistent_context = platform == "figma" and ("/design/" in url or "/file/" in url)
        if use_persistent_context:
            print("  🔐 Using your Chrome profile for Figma (logged-in session); browser window will appear briefly.")
        with sync_playwright() as p:
            if use_persistent_context:
                context = p.chromium.launch_persistent_context(
                    user_data_dir="/home/khyathi/.config/google-chrome",
                    headless=False,
                    args=["--no-sandbox"],
                    viewport={"width": 1440, "height": 900},
                )
                browser = None
            else:
                browser = p.chromium.launch(
                    headless=True,
                    args=[
                        '--no-sandbox',
                        '--disable-setuid-sandbox',
                        '--disable-dev-shm-usage',
                        '--disable-gpu',
                        '--no-zygote',
                        '--single-process',
                    ],
                )
                context = browser.new_context(viewport={"width": 1440, "height": 900})
            
            page = context.new_page()
            # Google Docs/Drive/Figma: use 'load' to avoid long waits; Figma canvas never reaches networkidle
            wait_condition = "load" if platform in ("google_docs", "google_drive", "figma") else "networkidle"
            page.goto(load_url, wait_until=wait_condition, timeout=60000)
            
            if platform == "google_docs":
                time.sleep(5)
            elif platform == "google_drive":
                time.sleep(2)
            elif platform == "figma":
                time.sleep(4)
            print("📋 Extracting profile metadata...")
            metadata = self.extract_profile_metadata(page, platform)
            metadata["url"] = url
            metadata["platform"] = platform
            if platform == "figma" and ("/design/" in url or "/file/" in url):
                metadata["figma_note"] = "Figma design file — may require auth, preview only"
            
            # PRO: Extract Design Specs
            print("🎨 Extracting technical design specs...")
            metadata["design_specs"] = self.extract_design_specs(page)
            
            # Name folder after candidate
            name_slug = metadata.get("name", "unknown").replace(" ", "_").lower()
            if run_id:
                folder_name = f"{name_slug}_{run_id}"
            else:
                folder_name = name_slug
            
            self.current_snapshots_dir = os.path.join(self.base_snapshots_dir, folder_name)
            os.makedirs(self.current_snapshots_dir, exist_ok=True)
            print(f"📁 Snapshots will be saved in: {folder_name}")

            print("🔍 Discovering projects...")
            projects = self.discover_projects(page, platform, candidate_role=candidate_role)
            print(f"✅ Discovered {len(projects)} projects.")
            # For Figma design/file: reuse this page for the single snapshot (avoid 2nd load and timeout race)
            reuse_page = None
            if platform == "figma" and ("/design/" in url or "/file/" in url) and projects:
                reuse_page = page
                print("  📌 Reusing open page for Figma snapshot (single load).")
            else:
                page.close()

            # Brand Designer: use Gemini to select 3 most relevant projects from all discovered
            if candidate_role and "brand" in candidate_role.lower() and genai_client and projects:
                selected = select_brand_projects_with_ai(projects, candidate_role, genai_client)
                reserve = [p for p in projects if p not in selected]
                role_filter_note = None
            else:
                selected, role_filter_note, reserve = _filter_projects_for_role(projects, candidate_role)
            if role_filter_note:
                metadata["role_filter_note"] = role_filter_note
                print(f"  ⚠️ {role_filter_note} — using top 3 projects overall.")
            if platform == "framer" and selected:
                base_norm = url.rstrip("/") or url
                has_homepage = any((p.get("url") or "").rstrip("/") == base_norm for p in selected)
                if not has_homepage and projects:
                    homepage = next((p for p in projects if (p.get("url") or "").rstrip("/") == base_norm), None)
                    if homepage:
                        selected = [homepage] + [p for p in selected if (p.get("url") or "").rstrip("/") != base_norm][:2]
            if candidate_role and selected:
                print(f"🎯 Found {len(selected)} {candidate_role}-relevant project(s). Evaluating those.")
                for idx, p in enumerate(selected, 1):
                    print(f"   {idx}. {p.get('title', 'Untitled')[:60]}")

            # Framer: full page scroll for homepage (treat as portfolio); no section-only clipping
            base_normalized = url.rstrip("/") or url
            for project in selected:
                project_url_norm = (project.get("url") or "").rstrip("/")
                if project_url_norm == base_normalized and platform == "framer":
                    project["capture_section_only"] = False

            # Snapshot up to 3 projects; if one fails, replace with next from reserve
            queue = list(selected) + list(reserve)
            final_projects = []
            slot = 0
            while len(final_projects) < 3 and queue:
                project = queue.pop(0)
                safe_title = "".join([c if c.isalnum() else "_" for c in project["title"]])[:30]
                with sync_playwright() as p_snap:
                    snap_browser = p_snap.chromium.launch(
                        headless=True,
                        args=['--no-sandbox','--disable-setuid-sandbox',
                              '--disable-dev-shm-usage','--disable-gpu',
                              '--no-zygote','--single-process'],
                    )
                    snap_context = snap_browser.new_context(viewport={"width": 1440, "height": 900})
                    try:
                        imgs, text = self.snapshot_project(
                            snap_context, project["url"], f"proj_{slot}_{safe_title}",
                            capture_section_only=project.get("capture_section_only", False),
                            candidate_role=candidate_role,
                            existing_page=None
                        )
                    finally:
                        snap_browser.close()

                if imgs:
                    project["screenshots"] = imgs
                    project["case_study_text"] = text
                    final_projects.append(project)
                    slot += 1
                else:
                    print(f"  ⚠️ Snapshot failed — trying next candidate from list.")
            projects = final_projects

            if browser is not None:
                browser.close()
            else:
                context.close()
            
        return metadata, projects, folder_name