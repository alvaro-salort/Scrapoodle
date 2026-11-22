"""
Parser de alto rendimiento para Moodle 4.x: Extracción de cursos, secciones, actividades y recursos.
Utiliza selectolax (motor Lexbor en C) para parseo DOM ultrarrápido (30x-40x más veloz que BeautifulSoup).
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
from urllib.parse import urljoin, parse_qs, urlparse

from selectolax.lexbor import LexborHTMLParser

from moodle_scraper.utils import sanitize_filename, make_absolute_url, format_bytes


@dataclass
class FileItem:
    """Representa un archivo descargable."""
    name: str
    url: str
    size_hint: Optional[str] = None
    section_name: str = ""
    source_module: str = ""
    local_name: str = ""
    size_bytes: Optional[int] = None
    timemodified: Optional[int] = None


@dataclass
class CourseModule:
    """Representa una actividad o recurso dentro de una sección de Moodle."""
    id: str
    mod_type: str  # 'resource', 'folder', 'page', 'url', 'label', 'assign', 'forum', 'book', 'other'
    name: str
    url: Optional[str] = None
    description_html: str = ""
    description_text: str = ""
    content_html: str = ""
    content_text: str = ""
    external_url: Optional[str] = None
    files: List[FileItem] = field(default_factory=list)


@dataclass
class CourseSection:
    """Representa una sección, tema o semana de un curso."""
    id: str
    section_number: int
    name: str
    summary_html: str = ""
    summary_text: str = ""
    modules: List[CourseModule] = field(default_factory=list)
    parent_name: Optional[str] = None

    @property
    def folder_name(self) -> str:
        """Nombre limpio de la carpeta de la sección."""
        clean = sanitize_filename(self.name)
        if not clean or clean.lower() in ["sección", "tema", "section"]:
            return f"Tema_{self.section_number}"
        return clean

    @property
    def relative_folder_path(self) -> Path:
        """Genera la ruta de carpetas relativa respetando la jerarquía padre/hijo."""
        clean_name = self.folder_name
        if self.parent_name:
            clean_parent = sanitize_filename(self.parent_name)
            if clean_parent and clean_parent.lower() != clean_name.lower():
                return Path(clean_parent) / clean_name
        return Path(clean_name)


@dataclass
class OnetopicTabInfo:
    """Información de navegación de una pestaña de Onetopic / Árbol de pestañas."""
    section_number: int
    title: str
    url: str
    parent_name: Optional[str] = None
    level: int = 0


@dataclass
class Course:
    """Representa un curso de Moodle."""
    id: str
    name: str
    shortname: str = ""
    url: str = ""

    @property
    def sanitized_name(self) -> str:
        """Nombre normalizado para la carpeta local en el sistema de archivos."""
        return sanitize_filename(self.name)


class MoodleParser:
    """Colección de métodos de parseo de alto rendimiento con Lexbor (selectolax)."""

    @staticmethod
    def parse_courses_from_html(html: str, base_url: str) -> List[Course]:
        """Extrae la lista de cursos desde páginas HTML como my/courses.php o my/."""
        tree = LexborHTMLParser(html)
        courses: Dict[str, Course] = {}

        # 1. Enlaces directos a course/view.php?id=X
        for a_tag in tree.css("a"):
            href = a_tag.attributes.get("href", "")
            if not href or "course/view.php" not in href or "id=" not in href:
                continue

            full_url = make_absolute_url(base_url, href)
            parsed = urlparse(full_url)
            params = parse_qs(parsed.query)
            course_id = params.get("id", [""])[0]

            if not course_id or course_id == "1":
                continue

            title = a_tag.text(strip=True)
            if not title:
                title = a_tag.attributes.get("title", "") or a_tag.attributes.get("aria-label", "")
                if not title and a_tag.parent:
                    title = a_tag.parent.text(strip=True)

            title = re.sub(r'^(Curso|Course)\s*:\s*', '', title, flags=re.IGNORECASE)
            title = re.sub(r'\s+', ' ', title).strip()

            if title and len(title) > 2 and course_id not in courses:
                courses[course_id] = Course(id=course_id, name=title, url=full_url)

        # 2. Tarjetas de curso en Moodle 4.x (.dashboard-card, .coursebox)
        for card in tree.css(".dashboard-card, .coursebox"):
            link = card.css_first("a[href*='course/view.php']")
            if link:
                href = link.attributes.get("href", "")
                full_url = make_absolute_url(base_url, href)
                course_id = parse_qs(urlparse(full_url).query).get("id", [""])[0]

                name_elem = card.css_first(".coursename, .course-title, .coursename-text") or link
                name = name_elem.text(strip=True)

                if course_id and name and course_id not in courses:
                    courses[course_id] = Course(id=course_id, name=name, url=full_url)

        return list(courses.values())

    @staticmethod
    def parse_courses_from_ajax(json_data: List[Dict[str, Any]], base_url: str) -> List[Course]:
        """Extrae cursos desde la respuesta JSON de lib/ajax/service.php."""
        courses = []
        if not isinstance(json_data, list):
            return courses
        for item in json_data:
            if not isinstance(item, dict) or item.get("error"):
                continue
            data = item.get("data", {})
            if not isinstance(data, dict):
                continue
            course_list = data.get("courses", [])

            for c in course_list:
                if not isinstance(c, dict):
                    continue
                cid = str(c.get("id", ""))
                fullname = c.get("fullname", "") or c.get("displayname", "") or c.get("shortname", "")
                view_url = c.get("viewurl", "") or f"course/view.php?id={cid}"
                full_url = make_absolute_url(base_url, view_url)

                if cid and fullname:
                    courses.append(Course(
                        id=cid,
                        name=fullname,
                        shortname=c.get("shortname", ""),
                        url=full_url
                    ))
        return courses

    @staticmethod
    def parse_course_contents_from_ajax(json_data: Any, base_url: str) -> List[CourseSection]:
        """Extrae secciones y módulos desde core_course_get_contents (AJAX)."""
        sections: List[CourseSection] = []
        raw_sections: List[Any] = []

        if isinstance(json_data, list):
            for item in json_data:
                if not isinstance(item, dict) or item.get("error"):
                    continue
                if "data" in item:
                    inner = item.get("data")
                    if isinstance(inner, list):
                        raw_sections = inner
                        break
                elif "modules" in item or "section" in item:
                    raw_sections = [
                        s for s in json_data
                        if isinstance(s, dict) and not s.get("error") and ("modules" in s or "section" in s)
                    ]
                    break
        elif isinstance(json_data, dict) and not json_data.get("error"):
            inner = json_data.get("data", [])
            if isinstance(inner, list):
                raw_sections = inner

        for sec in raw_sections:
            if not isinstance(sec, dict) or sec.get("error"):
                continue
            sec_id = str(sec.get("id", ""))
            sec_num = int(sec.get("section", 0))
            name = sec.get("name", "") or f"Tema {sec_num}"
            summary = sec.get("summary", "") or ""

            clean_title = re.sub(r'<[^>]+>', '', name).strip() or f"Tema {sec_num}"
            clean_title = re.sub(r'^(Tema|Sección|Section)\s*:\s*', '', clean_title, flags=re.IGNORECASE)
            clean_title = re.sub(r'\s+', ' ', clean_title).strip()

            modules: List[CourseModule] = []
            for mod in sec.get("modules", []):
                if not isinstance(mod, dict):
                    continue
                mid = str(mod.get("id", ""))
                modname = mod.get("modname", "other")
                raw_mname = mod.get("name", "") or f"Actividad_{mid}"
                mname = re.sub(r'<[^>]+>', '', raw_mname).strip()
                murl = mod.get("url") or ""
                if murl:
                    murl = make_absolute_url(base_url, murl)
                mdesc = mod.get("description", "") or ""

                files: List[FileItem] = []
                for content in mod.get("contents", []):
                    if not isinstance(content, dict):
                        continue
                    furl = content.get("fileurl") or ""
                    if furl:
                        furl = make_absolute_url(base_url, furl)
                        fname = content.get("filename") or furl.split("/")[-1].split("?")[0]
                        fsize = content.get("filesize")
                        ftime = content.get("timemodified")
                        size_str = format_bytes(fsize) if fsize else None
                        files.append(FileItem(
                            name=fname,
                            url=furl,
                            size_hint=size_str,
                            size_bytes=int(fsize) if fsize else None,
                            timemodified=int(ftime) if ftime else None,
                            section_name=clean_title,
                            source_module=mname
                        ))

                modules.append(CourseModule(
                    id=mid,
                    mod_type=modname,
                    name=mname,
                    url=murl if murl else None,
                    description_html=mdesc,
                    description_text=re.sub(r'<[^>]+>', '', mdesc).strip(),
                    files=files
                ))

            sections.append(CourseSection(
                id=sec_id or f"section-{sec_num}",
                section_number=sec_num,
                name=clean_title,
                summary_html=summary,
                summary_text=re.sub(r'<[^>]+>', '', summary).strip(),
                modules=modules
            ))

        return sections

    @staticmethod
    def extract_section_links_from_html(html: str, base_url: str, course_id: str) -> List[Tuple[int, str, str]]:
        """Detecta enlaces a pestañas o sub-secciones en formatos multi-página."""
        tree = LexborHTMLParser(html)
        links: List[Tuple[int, str, str]] = []
        seen_urls = set()

        for a_tag in tree.css("a"):
            href = a_tag.attributes.get("href", "")
            if not href or "course/view.php" not in href:
                continue

            full_url = make_absolute_url(base_url, href)
            if full_url in seen_urls:
                continue

            parsed = urlparse(full_url)
            params = parse_qs(parsed.query)

            cid = params.get("id", [""])[0]
            if cid and str(cid) != str(course_id):
                continue

            if "section" not in params and "sectionid" not in params:
                continue

            seen_urls.add(full_url)
            sec_num = 0
            if "section" in params:
                try:
                    sec_num = int(params["section"][0])
                except ValueError:
                    pass

            sec_title = a_tag.text(separator=" ", strip=True)
            sec_title = re.sub(r'\s+', ' ', sec_title).strip()
            links.append((sec_num, sec_title, full_url))

        links.sort(key=lambda x: x[0])
        return links

    @staticmethod
    def is_onetopic_course(html: str) -> bool:
        """Determina si un curso utiliza el formato de pestañas onetopic."""
        tree = LexborHTMLParser(html)
        if tree.css_first("#tabs-tree-start"):
            return True
        if tree.css_first(".onetopic, .format-onetopic, .format_onetopic, .format_onetopic-tabs"):
            return True
        for li in tree.css("li"):
            cls = li.attributes.get("class", "") or ""
            if any(k in cls for k in ["tab_position", "tab_level", "subtopic"]):
                return True
        return False

    @staticmethod
    def extract_onetopic_level_0_tabs(html: str, base_url: str, course_id: str) -> List[Dict[str, Any]]:
        """Extrae pestañas de nivel raíz en formato onetopic."""
        tree = LexborHTMLParser(html)
        tabs: List[Dict[str, Any]] = []

        for li in tree.css("li"):
            cls = li.attributes.get("class", "") or ""
            classes = cls.split()
            if "tab_level_1" in classes or "subtopic" in classes:
                continue
            if "tab_level_0" not in classes and "tab_position" not in cls:
                continue

            a = li.css_first("a")
            if not a or not a.attributes.get("href"):
                continue

            is_disabled = "disabled" in classes or "dimmed" in classes
            has_childs = "haschilds" in classes

            # Remover textos accesibles ocultos para obtener el título limpio
            for hide in a.css(".accesshide, .sr-only"):
                hide.decompose()
            tab_title = re.sub(r'\s+', ' ', a.text(separator=" ", strip=True)).strip()

            full_url = make_absolute_url(base_url, a.attributes.get("href", ""))
            parsed = urlparse(full_url)
            params = parse_qs(parsed.query)

            cid = params.get("id", [""])[0]
            if cid and str(cid) != str(course_id):
                continue

            sec_num = 0
            if "section" in params:
                try:
                    sec_num = int(params["section"][0])
                except ValueError:
                    pass

            tabs.append({
                "section": sec_num,
                "title": tab_title,
                "url": full_url,
                "haschilds": has_childs,
                "disabled": is_disabled
            })
        return tabs

    @staticmethod
    def extract_onetopic_level_1_tabs(html: str, base_url: str, parent_title: str) -> List[OnetopicTabInfo]:
        """Extrae subpestañas hijas (nivel 1) en onetopic."""
        tree = LexborHTMLParser(html)
        sub_tabs: List[OnetopicTabInfo] = []

        for li in tree.css("li"):
            cls = li.attributes.get("class", "") or ""
            classes = cls.split()
            if "tab_level_1" not in classes and "subtopic" not in classes:
                continue

            is_disabled = "disabled" in classes or "dimmed" in classes
            a = li.css_first("a")
            if not a or not a.attributes.get("href"):
                continue

            for hide in a.css(".accesshide, .sr-only"):
                hide.decompose()
            tab_title = re.sub(r'\s+', ' ', a.text(separator=" ", strip=True)).strip()

            full_url = make_absolute_url(base_url, a.attributes.get("href", ""))
            parsed = urlparse(full_url)
            params = parse_qs(parsed.query)
            sec_num = 0
            if "section" in params:
                try:
                    sec_num = int(params["section"][0])
                except ValueError:
                    pass

            if not is_disabled:
                sub_tabs.append(OnetopicTabInfo(
                    section_number=sec_num,
                    title=tab_title,
                    url=full_url,
                    parent_name=parent_title,
                    level=1
                ))
        return sub_tabs

    @staticmethod
    def _filter_section_candidates(candidates: list) -> list:
        """Filtra contenedores de sección excluyendo nodos internos (img-text, summary, activity) y descendientes."""
        excluded = {"sectionname", "summary", "section-summary", "activity", "activityinstance", "contentwithoutlink", "img-text"}
        filtered = []
        for cand in candidates:
            cls_tokens = set((cand.attributes.get("class", "") or "").split())
            if cls_tokens & excluded:
                continue
            filtered.append(cand)

        top_level = []
        for cand in filtered:
            p = cand.parent
            is_nested = False
            while p is not None:
                if any(p == other for other in filtered if other != cand):
                    is_nested = True
                    break
                p = p.parent
            if not is_nested:
                top_level.append(cand)
        return top_level

    @staticmethod
    def parse_course_sections(
        html: str,
        base_url: str,
        target_section_number: Optional[int] = None,
        section_title: Optional[str] = None,
        parent_name: Optional[str] = None
    ) -> List[CourseSection]:
        """Parsea la vista de un curso extrayendo secciones, actividades y recursos."""
        tree = LexborHTMLParser(html)
        sections: List[CourseSection] = []

        if target_section_number is not None:
            sec_node = tree.css_first(
                f"li#section-{target_section_number}, div#section-{target_section_number}, section#section-{target_section_number}, #section-{target_section_number}"
            )
            if not sec_node:
                raw_cands = tree.css("li.section, div.section, section.section, .course-section")
                candidates = MoodleParser._filter_section_candidates(raw_cands)
                for cand in candidates:
                    cand_id = cand.attributes.get("id", "") or ""
                    if f"section-{target_section_number}" in cand_id or str(target_section_number) in cand_id:
                        sec_node = cand
                        break
                if not sec_node and candidates:
                    sec_node = candidates[0]

            if not sec_node:
                sec_node = tree.css_first(".course-content, #region-main") or tree.body

            final_title = section_title
            if not final_title and sec_node:
                for sel in (".sectionname", ".section-title", ".coursesection-title", "h3.section-title", "h3", "h4", "h2"):
                    title_elem = sec_node.css_first(sel)
                    if title_elem and title_elem.text(strip=True):
                        title_tree = LexborHTMLParser(title_elem.html or "")
                        for hide in title_tree.css(".accesshide, .sr-only"):
                            hide.decompose()
                        final_title = title_tree.text(separator=" ", strip=True)
                        if final_title:
                            break
                if not final_title:
                    title_tag = tree.css_first("title")
                    if title_tag and title_tag.text(strip=True):
                        match_title = re.search(r"Tema:\s*([^|]+)", title_tag.text(strip=True))
                        if match_title:
                            final_title = match_title.group(1).strip()
            if not final_title:
                final_title = "General" if target_section_number == 0 else f"Tema {target_section_number}"

            if sec_node:
                section_obj = MoodleParser._parse_single_section_node(
                    sec_node,
                    base_url,
                    default_number=target_section_number,
                    override_title=final_title,
                    parent_name=parent_name
                )
                if section_obj:
                    section_obj.id = f"section-{target_section_number}"
                    section_obj.section_number = target_section_number
                    sections.append(section_obj)
            return sections

        # Flujo multi-sección estándar
        raw_cands = tree.css("li.section, div.section, section.section, li.course-section, div.course-section, section.course-section")
        sec_nodes = MoodleParser._filter_section_candidates(raw_cands)
        if not sec_nodes:
            sec_nodes = MoodleParser._filter_section_candidates(tree.css("[id^='section-']"))
        if not sec_nodes:
            content_container = tree.css_first(".course-content, #region-main")
            if content_container:
                sec_nodes = [content_container]

        seen_section_ids = set()
        sec_counter = 0

        for s_node in sec_nodes:
            sec_id_attr = s_node.attributes.get("id", "") or ""
            sec_num_match = re.search(r"section-(\d+)", sec_id_attr)
            if sec_num_match:
                section_num = int(sec_num_match.group(1))
            else:
                section_num = sec_counter

            sec_key = f"{section_num}_{sec_id_attr}"
            if sec_key in seen_section_ids:
                continue
            seen_section_ids.add(sec_key)
            sec_counter += 1

            sec_obj = MoodleParser._parse_single_section_node(
                s_node,
                base_url,
                default_number=section_num,
                parent_name=parent_name
            )
            if sec_obj:
                sections.append(sec_obj)

        return sections

    @staticmethod
    def _parse_single_section_node(
        s_node,
        base_url: str,
        default_number: int = 0,
        override_title: Optional[str] = None,
        parent_name: Optional[str] = None
    ) -> Optional[CourseSection]:
        """Extrae la información de un nodo DOM de sección."""
        sec_id = s_node.attributes.get("id", "") or f"section-{default_number}"
        sec_num = default_number
        match = re.search(r'section-(\d+)', sec_id)
        if match:
            sec_num = int(match.group(1))

        title = override_title
        if not title:
            for sel in (".sectionname", ".section-title", ".coursesection-title", "h3", "h4", "h2"):
                title_node = s_node.css_first(sel)
                if title_node and title_node.text(strip=True):
                    title_tree = LexborHTMLParser(title_node.html or "")
                    for hide in title_tree.css(".accesshide, .sr-only"):
                        hide.decompose()
                    title = title_tree.text(separator=" ", strip=True)
                    if title:
                        break
            if not title:
                title = "General" if sec_num == 0 else f"Tema {sec_num}"

        clean_title = re.sub(r'^(Tema|Sección|Section)\s*:\s*', '', title, flags=re.IGNORECASE)
        clean_title = re.sub(r'\s+', ' ', clean_title).strip()

        # Resumen
        summary_node = None
        for sel in (".summary", ".section-summary", ".summarytext"):
            cand = s_node.css_first(sel)
            if cand:
                summary_node = cand
                break
        summary_html = (summary_node.html or "") if summary_node else ""
        summary_text = summary_node.text(separator="\n", strip=True) if summary_node else ""

        raw_activities = s_node.css("li.activity, div.activity, li.activity-item, div.activity-item")
        activities = []
        for act in raw_activities:
            p = act.parent
            is_nested = False
            while p is not None and p != s_node:
                if any(p == other for other in raw_activities if other != act):
                    is_nested = True
                    break
                p = p.parent
            if not is_nested:
                activities.append(act)

        modules: List[CourseModule] = []
        seen_mod_keys = set()
        for act in activities:
            mod = MoodleParser._parse_activity_node(act, base_url, clean_title)
            if mod:
                mod_key = mod.url or mod.id or mod.name
                if mod_key in seen_mod_keys:
                    continue
                seen_mod_keys.add(mod_key)
                modules.append(mod)

        return CourseSection(
            id=sec_id,
            section_number=sec_num,
            name=clean_title,
            summary_html=summary_html,
            summary_text=summary_text,
            modules=modules,
            parent_name=parent_name
        )

    @staticmethod
    def _parse_activity_node(act_node, base_url: str, section_name: str) -> Optional[CourseModule]:
        """Extrae la información de una actividad o recurso desde su nodo DOM."""
        mid = act_node.attributes.get("id", "") or ""
        clean_id = re.sub(r'^module-', '', mid)

        cls = act_node.attributes.get("class", "") or ""
        node_html = act_node.html or ""
        mod_type = "other"
        for t in ["resource", "folder", "page", "url", "label", "assign", "forum", "book", "quiz", "glossary"]:
            if f"modtype_{t}" in cls or f"type_{t}" in cls or f"/{t}/" in node_html:
                mod_type = t
                break

        link = None
        for a_tag in act_node.css("a"):
            href = a_tag.attributes.get("href", "") or ""
            if re.search(r"mod/\w+/view\.php\?id=\d+", href):
                link = a_tag
                break
        if not link and mod_type != "label":
            link = act_node.css_first("a.aalink, a.aalmodal, a")

        url = None
        if link and mod_type != "label":
            href = link.attributes.get("href")
            if href:
                url = make_absolute_url(base_url, href)

        if url and mod_type == "other":
            match = re.search(r"mod/(\w+)/view\.php", url)
            if match:
                mod_type = match.group(1)

        name = ""
        name_elem = None
        for sel in (".instancename", ".activityname", ".activity-item-title"):
            cand = act_node.css_first(sel)
            if cand:
                name_elem = cand
                break
        if name_elem is None and mod_type != "label":
            name_elem = link

        if name_elem is not None:
            name_tree = LexborHTMLParser(name_elem.html or "")
            for hide in name_tree.css(".accesshide, .sr-only"):
                hide.decompose()
            name = name_tree.text(separator=" ", strip=True)

        desc_node = None
        for sel in (".contentafterlink", ".activity-description", ".description", ".intro", ".contentwithoutlink"):
            cand = act_node.css_first(sel)
            if cand:
                desc_node = cand
                break
        desc_html = (desc_node.html or "") if desc_node else ""
        desc_text = desc_node.text(separator="\n", strip=True) if desc_node else ""

        content_html = ""
        content_text = ""
        if mod_type == "label" or not url:
            label_content = None
            for sel in (".contentwithoutlink", ".mod-indent-outer", ".activity-description", ".activity-altcontent"):
                cand = act_node.css_first(sel)
                if cand:
                    label_content = cand
                    break
            if label_content is None:
                label_content = act_node
            content_html = label_content.html or ""
            content_text = label_content.text(separator="\n", strip=True)
            if mod_type == "label":
                desc_html = ""
                desc_text = ""
            if not name:
                first_line = [l.strip() for l in content_text.splitlines() if l.strip()]
                name = first_line[0][:60] if first_line else "Nota / Explicación"

        if not name and not content_text and not url:
            return None

        if not name:
            name = f"Recurso_{mod_type}_{clean_id or 'item'}"

        name = re.sub(r'\s+', ' ', name).strip()
        name = re.sub(r'\s+Tarea$', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s+Archivo$', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s+Carpeta$', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s+Página$', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s+URL$', '', name, flags=re.IGNORECASE)

        files: List[FileItem] = []
        for file_link in act_node.css("a"):
            href = file_link.attributes.get("href", "") or ""
            if "pluginfile.php" in href:
                f_url = make_absolute_url(base_url, href)
                f_name = file_link.text(strip=True) or f_url.split("/")[-1].split("?")[0]
                files.append(FileItem(
                    name=f_name,
                    url=f_url,
                    section_name=section_name,
                    source_module=name
                ))

        return CourseModule(
            id=clean_id,
            mod_type=mod_type,
            name=name,
            url=url,
            description_html=desc_html,
            description_text=desc_text,
            content_html=content_html,
            content_text=content_text,
            files=files
        )

    @staticmethod
    def parse_folder_files(html: str, base_url: str, section_name: str, module_name: str) -> List[FileItem]:
        """Extrae los archivos contenidos dentro de una carpeta (/mod/folder/view.php)."""
        tree = LexborHTMLParser(html)
        files: List[FileItem] = []

        folder_tree = None
        for sel in (".foldertree", ".generalbox", "div[role='main']", "#region-main"):
            cand = tree.css_first(sel)
            if cand:
                folder_tree = cand
                break
        if folder_tree is None:
            folder_tree = tree.body
        if folder_tree:
            for a_tag in folder_tree.css("a"):
                href = a_tag.attributes.get("href", "")
                if "pluginfile.php" not in href:
                    continue

                file_url = make_absolute_url(base_url, href)
                file_name = a_tag.text(strip=True)
                if not file_name or "." not in file_name:
                    file_name = file_url.split("/")[-1].split("?")[0]
                file_name = re.sub(r'\s+', ' ', file_name).strip()

                size_hint = None
                size_span = a_tag.parent.css_first(".filesize") if a_tag.parent else None
                if not size_span and a_tag.parent and a_tag.parent.parent:
                    size_span = a_tag.parent.parent.css_first(".filesize")
                if size_span:
                    size_hint = size_span.text(strip=True)

                if not any(f.url == file_url for f in files):
                    files.append(FileItem(
                        name=file_name,
                        url=file_url,
                        size_hint=size_hint,
                        section_name=section_name,
                        source_module=module_name
                    ))

        if not files:
            zip_form = tree.css_first("form[action*='download_folder.php']")
            if zip_form:
                action_url = make_absolute_url(base_url, zip_form.attributes.get("action", ""))
                inputs = {
                    inp.attributes.get("name"): inp.attributes.get("value", "")
                    for inp in zip_form.css("input")
                    if inp.attributes.get("name")
                }
                query_str = "&".join(f"{k}={v}" for k, v in inputs.items())
                zip_url = f"{action_url}?{query_str}" if query_str else action_url

                files.append(FileItem(
                    name=f"{sanitize_filename(module_name)}.zip",
                    url=zip_url,
                    section_name=section_name,
                    source_module=module_name
                ))

        return files

    @staticmethod
    def parse_external_url(html: str) -> Optional[str]:
        """Extrae el enlace de destino real desde la vista /mod/url/view.php."""
        tree = LexborHTMLParser(html)
        url_div = tree.css_first(".urlworkaround")
        if url_div:
            a_tag = url_div.css_first("a")
            if a_tag and a_tag.attributes.get("href"):
                return a_tag.attributes.get("href")

        main_box = tree.css_first("div[role='main']") or tree.css_first("#region-main")
        if main_box:
            for a in main_box.css("a"):
                href = a.attributes.get("href", "")
                if href.startswith(("http://", "https://")):
                    if "moodle" not in href and "logout" not in href and "course" not in href:
                        return href

        return None

    @staticmethod
    def _parse_activity_element(elem, base_url: str, section_name: str) -> Optional[CourseModule]:
        """Compatibilidad con BeautifulSoup tags y selectolax nodes."""
        html_str = str(elem)
        tree = LexborHTMLParser(html_str)
        node = tree.css_first("li.activity, div.activity") or (tree.body.child if tree.body else None)
        if node:
            return MoodleParser._parse_activity_node(node, base_url, section_name)
        return None

    @staticmethod
    def parse_page_content_html(html: str) -> Dict[str, str]:
        """Extrae el contenido de una página teórica (/mod/page/view.php)."""
        tree = LexborHTMLParser(html)
        title = ""
        for sel in ("h2.page-title", "h3.page-title", ".page-title", "h2", "h3", ".page-header-headings h1"):
            title_elem = tree.css_first(sel)
            if title_elem and title_elem.text(strip=True):
                title = title_elem.text(strip=True)
                break

        box = None
        for sel in (".page-content", ".generalbox", "div[role='main']", "#region-main"):
            cand = tree.css_first(sel)
            if cand:
                box = cand
                break
        if box:
            for nav in box.css(".activity-navigation, .breadcrumb, .modified"):
                nav.decompose()
            return {
                "title": title,
                "html": box.html or "",
                "text": box.text(separator="\n", strip=True) or ""
            }
        return {"title": title, "html": "", "text": ""}

    @staticmethod
    def parse_page_content(html: str) -> Dict[str, str]:
        """Alias compatible de parse_page_content_html."""
        return MoodleParser.parse_page_content_html(html)

    @staticmethod
    def clean_html_to_text(html: str) -> str:
        """Convierte fragmentos HTML a texto limpio removiendo etiquetas y scripts."""
        if not html:
            return ""
        tree = LexborHTMLParser(html)
        for tag in tree.css("script, style, noscript, svg, head"):
            tag.decompose()
        return tree.text(separator=" ", strip=True)
