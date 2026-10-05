r"""data 폴더(DB + 원본 문서) 백업 → C:\work\docchat\backups\data-날짜-시각\  (최근 14개만 보관)
서버가 켜져 있어도 안전하게 DB를 복사한다(SQLite 백업 기능 사용). 작업 스케줄러 'docchat 백업'이 매일 실행.
이어서 노트북 밖(OneDrive 폴더 → 클라우드 자동 업로드)에도 복사한다. 로그인 비밀 키(jwt.secret)는 클라우드로 보내지 않는다
(없어도 복원되며, 복원하면 모두 한 번 다시 로그인). 위치는 환경변수 DOCCHAT_OFFSITE 로 바꿀 수 있다.
    ..\server\.venv\Scripts\python.exe backup_data.py
"""
import os, shutil, sqlite3, sys
from datetime import datetime
from pathlib import Path

KEEP = 14
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "server" / "data"
BACKUPS = ROOT / "backups"
_od = os.environ.get("DOCCHAT_OFFSITE") or (os.path.join(os.environ["OneDrive"], "docchat-backups") if os.environ.get("OneDrive") else "")
OFFSITE = Path(_od) if _od else None
NOT_OFFSITE = {"jwt.secret"}  # 클라우드로 보내지 않는 파일


def prune(folder):
    olds = sorted(d for d in folder.glob("data-*") if d.is_dir())
    for d in olds[:-KEEP]:
        shutil.rmtree(d)
    return min(len(olds), KEEP)


def copy_offsite(src):
    """로컬 백업 한 벌을 OneDrive 폴더로 복사(비밀 키 제외). 실패해도 로컬 백업은 그대로 둔다."""
    if not OFFSITE:
        return "노트북 밖 복사: 위치 없음(OneDrive 미설치)"
    try:
        OFFSITE.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, OFFSITE / src.name, ignore=lambda d, names: [n for n in names if n in NOT_OFFSITE],
                        dirs_exist_ok=True)
        return f"노트북 밖 복사: {OFFSITE / src.name} · 보관 {prune(OFFSITE)}개"
    except Exception as e:
        return f"노트북 밖 복사 실패: {e}"


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
    kept = prune(BACKUPS)
    n = sum(1 for _ in dest.rglob("*") if _.is_file())
    size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file()) / 1e6
    msg = (f"{datetime.now():%Y-%m-%d %H:%M} 백업 완료: {dest} ({n}개 파일, {size:.1f}MB) · 보관 {kept}개 · "
           + copy_offsite(dest))
    print(msg)
    with open(BACKUPS / "backup.log", "a", encoding="utf-8") as f:  # 창 없이 실행돼도 결과가 남도록
        f.write(msg + "\n")


if __name__ == "__main__":
    main()
