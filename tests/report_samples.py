"""Hata ayıklama: verilen dosyaları hazırlar (kaydetmeden), kapıları ve uyarıları yazdırır.

Kullanım: python tests/report_samples.py DOSYA [DOSYA ...]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from metaclean.core.session import Job

for path in sys.argv[1:]:
    job = Job(path)
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
