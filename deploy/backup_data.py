r"""data 폴더(DB + 원본 문서) 백업 → C:\work\docchat\backups\data-날짜-시각\  (최근 14개만 보관)
서버가 켜져 있어도 안전하게 DB를 복사한다(SQLite 백업 기능 사용). 작업 스케줄러 'docchat 백업'이 매일 실행.
    ..\server\.venv\Scripts\python.exe backup_data.py
"""
import shutil, sqlite3, sys
from datetime import datetime
from pathlib import Path

KEEP = 14
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "server" / "data"
BACKUPS = ROOT / "backups"


def main():
    if not (DATA / "docchat.db").exists():
        sys.exit(f"DB가 없어요: {DATA / 'docchat.db'}")
    dest = BACKUPS / f"data-{datetime.now():%Y%m%d-%H%M}"
    dest.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(DATA / "docchat.db")
    out = sqlite3.connect(dest / "docchat.db")
    with out:
        src.backup(out)  # 쓰는 중이어도 일관된 상태로 복사
    src.close(); out.close()
    for p in DATA.iterdir():
        if p.name.startswith("docchat.db"):
            continue  # DB는 위에서 복사(작업 중 임시 파일 -wal/-journal 제외)
        (shutil.copytree if p.is_dir() else shutil.copy2)(p, dest / p.name)
    olds = sorted(d for d in BACKUPS.glob("data-*") if d.is_dir())
    for d in olds[:-KEEP]:
        shutil.rmtree(d)
    n = sum(1 for _ in dest.rglob("*") if _.is_file())
    size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file()) / 1e6
    msg = f"{datetime.now():%Y-%m-%d %H:%M} 백업 완료: {dest} ({n}개 파일, {size:.1f}MB) · 보관 {min(len(olds), KEEP)}개"
    print(msg)
    with open(BACKUPS / "backup.log", "a", encoding="utf-8") as f:  # 창 없이 실행돼도 결과가 남도록
        f.write(msg + "\n")


if __name__ == "__main__":
    main()
