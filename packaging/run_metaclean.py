"""PyInstaller giriş noktası (paket içinde göreli içe aktarma çalışsın diye ayrı dosya)."""
import sys

from metaclean.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
