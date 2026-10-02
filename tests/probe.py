"""Her örneği hazırlar (kaydetmeden) ve kapıları/kalan alanları yazdırır."""
import sys

from metaclean.core.handlers.base import Options
from metaclean.core.session import Job

for path in sys.argv[1:]:
    job = Job(path, Options(full_video_decode="--full" in sys.argv))
    r = job.prepare()
    print(f"\n=== {path.rsplit('/', 1)[-1]} [{r.fmt}] ok={r.ok} error={r.error}")
    print(f"  bulunan hassas alan: {len(r.found)}; silinen: {len(r.removed)}; korunan: {len(r.kept)}")
    for g in r.gates:
        print(f"  [{'OK' if g.passed else 'XX'}] {g.name}")
        if not g.passed:
            for d in g.details:
                print("       ", d)
    for w in r.warnings:
        print("  uyarı:", w)
    job.discard()
