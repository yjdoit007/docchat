"""올린 파일에서 본문 글자를 뽑는다: PDF, 한글(HWP 바이너리 / HWPX / HWPML).
extract(data) → (kind, texts)
  - PDF: texts = 페이지별 글자 (페이지 번호가 출처에 쓰인다)
  - 한글: texts = [문서 전체 글자] (한글 파일에는 고정된 페이지가 없어 페이지 번호를 쓰지 않는다)
"""
import html, io, re, struct, zipfile, zlib

import pymupdf

KINDS = {"pdf": ("application/pdf", ".pdf"),
         "hwp": ("application/x-hwp", ".hwp"),
         "hwpx": ("application/hwp+zip", ".hwpx"),
         "hwpml": ("application/x-hwpml", ".hwp"),
         "md": ("text/markdown; charset=utf-8", ".md")}  # 사용 안내 같은 글 문서(## 제목 단위로 나눔)


class DocError(ValueError):
    """사용자에게 그대로 보여 줄 수 있는 오류"""


def detect(data, filename=None):
    if filename and re.search(r"\.(md|txt)$", filename, re.I):
        try:
            data.decode("utf-8")
            return "md"
        except UnicodeDecodeError:
            return None
    if data.startswith(b"%PDF"):
        return "pdf"
    if data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):  # OLE(복합 문서) = HWP 5.0 바이너리
        return "hwp"
    if data.startswith(b"PK"):
        try:
            names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        except zipfile.BadZipFile:
            return None
        return "hwpx" if any(n.startswith("Contents/section") for n in names) else None
    head = data[:2048].lstrip(b"\xef\xbb\xbf").decode("utf-8", "ignore")
    if head.startswith("<?xml") and "<HWPML" in head:
        return "hwpml"
    return None


def extract(data, filename=None):
    kind = detect(data, filename)
    if not kind:
        raise DocError("PDF, 한글(HWP, HWPX), 글 문서(.md, .txt, UTF-8)만 올릴 수 있어요.")
    try:
        texts = {"pdf": _pdf, "hwp": _hwp, "hwpx": _hwpx, "hwpml": _hwpml,
                 "md": lambda d: [d.decode("utf-8").lstrip("﻿")]}[kind](data)
    except DocError:
        raise
    except Exception:
        raise DocError("파일을 읽지 못했어요. 파일이 손상됐거나 암호가 걸려 있는지 확인해 주세요.")
    if not any(t.strip() for t in texts):
        raise DocError("글자가 없는 파일이에요. 스캔 PDF라면 글자 인식(OCR)을 거쳐서 다시 올려 주세요."
                       if kind == "pdf" else "본문 글자를 찾지 못했어요.")
    return kind, texts


def _pdf(data):
    return [p.get_text() for p in pymupdf.open(stream=data, filetype="pdf")]


def _clean(lines):
    """문단 목록 → 본문. 빈 줄을 줄이고 문단마다 한 줄로."""
    out = [re.sub(r"[ \t ]+", " ", l).strip() for l in lines]
    return ["\n".join(l for l in out if l)]


# ---------- HWPML (한글 XML, 국가법령정보센터 'HWP 저장' 형식) ----------
def _walk(xml, text_re, para_end, drop):
    """XML을 앞에서부터 읽으며 글자 조각을 모으고, 문단 끝·표 칸 시작 표시마다 줄을 나눈다.
    문단 안에 표·머리글이 들어 있는(문단 속 문단) 구조에서도 글자를 빠뜨리지 않는다. drop 안의 내용(머리글·바닥글)은 뺀다."""
    xml = re.sub(drop, "", xml, flags=re.S)
    paras, cur = [], []
    for m in re.finditer(rf"{text_re}|{para_end}", xml, re.S):
        if m.group(1) is None:
            paras.append("".join(cur)); cur = []
        else:
            s = re.sub(r"<[^>]+>", lambda t: "\t" if "tab" in t.group().lower() else "", m.group(1))
            cur.append(html.unescape(s.replace("&nbsp;", " ")))
    paras.append("".join(cur))
    return _clean(paras)


def _hwpml(data):
    t = data.decode("utf-8", "replace")
    return _walk(t[t.find("<BODY"):], r"<CHAR\b[^>]*>(.*?)</CHAR>", r"</P>|<PARALIST\b",
                 r"<HEADER\b.*?</HEADER>|<FOOTER\b.*?</FOOTER>")


# ---------- HWPX (한글 2014 이후 기본 형식, XML을 zip으로 묶음) ----------
def _hwpx(data):
    z = zipfile.ZipFile(io.BytesIO(data))
    sections = sorted((n for n in z.namelist() if re.fullmatch(r"Contents/section\d+\.xml", n)),
                      key=lambda n: int(re.search(r"\d+", n).group()))
    text = []
    for name in sections:
        x = z.read(name).decode("utf-8", "replace")
        text += _walk(x, r"<hp:t\b[^>]*>(.*?)</hp:t>", r"</hp:p>|<hp:subList\b",
                      r"<hp:header\b.*?</hp:header>|<hp:footer\b.*?</hp:footer>")
    return ["\n".join(t for t in text if t)]


# ---------- HWP 5.0 바이너리 ----------
def _hwp(data):
    import olefile  # 한글 바이너리를 읽을 때만 필요
    ole = olefile.OleFileIO(io.BytesIO(data))
    header = ole.openstream("FileHeader").read()
    flags = struct.unpack_from("<I", header, 36)[0]
    if flags & 0x2:
        raise DocError("암호가 걸린 한글 파일이에요. 암호를 풀고 다시 올려 주세요.")
    if flags & 0x4:
        raise DocError("배포용(보기 전용)으로 저장된 한글 파일은 읽을 수 없어요. 일반 한글 파일로 다시 저장해 올려 주세요.")
    compressed = bool(flags & 0x1)
    sections = sorted((e for e in ole.listdir() if len(e) == 2 and e[0] == "BodyText" and e[1].startswith("Section")),
                      key=lambda e: int(e[1][7:]))
    paras = []
    for e in sections:
        raw = ole.openstream(e).read()
        if compressed:
            raw = zlib.decompress(raw, -15)
        pos = 0
        while pos + 4 <= len(raw):
            h = struct.unpack_from("<I", raw, pos)[0]
            tag, size = h & 0x3FF, (h >> 20) & 0xFFF
            pos += 4
            if size == 0xFFF:
                size = struct.unpack_from("<I", raw, pos)[0]
                pos += 4
            if tag == 67:  # HWPTAG_PARA_TEXT
                paras.append(_para_text(raw[pos:pos + size]))
            pos += size
    return _clean(paras)


# 한글 문단 글자 안의 조작 문자: 이 번호들은 뒤에 7글자(14바이트)짜리 정보가 붙는다
_EXT = {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}


def _para_text(b):
    out, i, n = [], 0, len(b) // 2
    while i < n:
        c = struct.unpack_from("<H", b, i * 2)[0]
        if c < 32:
            if c == 9:
                out.append("\t")
            elif c in (10, 13):
                out.append("\n")
            i += 8 if c in _EXT else 1
            continue
        out.append(chr(c))
        i += 1
    return "".join(out)
