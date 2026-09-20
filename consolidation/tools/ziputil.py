"""İç içe ZIP yardımcıları (Phase 1/2 ortak). Üye yolu 'a.zip::b/c.zip::d/e.csv' biçiminde olabilir: her '::' bir kat aşağı iner.
Kaynak zip'e yazılmaz; iç zip'ler bellekte (≤ NESTED_INMEM_LIMIT) ya da tmp dosyada açılır."""
import io, zipfile, shutil, hashlib, re
from pathlib import Path
NESTED_INMEM_LIMIT = 1_200_000_000
MAX_DEPTH = 4


def open_nested(zip_path, member_path, tmpdir=None):
    """(ZipFile, ZipInfo, cleanup_paths) döndürür: son katmanın ZipFile'ı ve hedef üyenin ZipInfo'su."""
    parts = member_path.split("::")
    z = zipfile.ZipFile(zip_path); cleanup = []
    for inner in parts[:-1]:
        zi = z.getinfo(inner)
        if zi.file_size <= NESTED_INMEM_LIMIT:
            z = zipfile.ZipFile(io.BytesIO(z.read(zi)))
        else:
            assert tmpdir is not None, "büyük iç zip için tmpdir gerekli"
            tmpdir = Path(tmpdir); tmpdir.mkdir(parents=True, exist_ok=True)
            tp = tmpdir / ("nested_" + re.sub(r"[^0-9A-Za-z_.]", "_", inner)[-100:])
            with z.open(zi) as src, open(tp, "wb") as dst: shutil.copyfileobj(src, dst, 4 << 20)
            cleanup.append(tp); z = zipfile.ZipFile(tp)
    return z, z.getinfo(parts[-1]), cleanup


def extract_member(zip_path, member_path, out_path, tmpdir=None):
    z, zi, cleanup = open_nested(zip_path, member_path, tmpdir)
    with z.open(zi) as src, open(out_path, "wb") as dst: shutil.copyfileobj(src, dst, 4 << 20)
    for c in cleanup: Path(c).unlink(missing_ok=True)
    return out_path


def member_sha256(zip_path, member_path, tmpdir=None):
    z, zi, cleanup = open_nested(zip_path, member_path, tmpdir)
    h = hashlib.sha256()
    with z.open(zi) as f:
        for chunk in iter(lambda: f.read(8 << 20), b""): h.update(chunk)
    for c in cleanup: Path(c).unlink(missing_ok=True)
    return h.hexdigest()
