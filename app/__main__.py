"""起動の入口。python -m app で動く。

役割：開発用のサーバーを起動する。
4つの極：特定の極に属さない土台。
守る原則：8（架空データだけを扱うデモとして動かす）。

127.0.0.1 だけで待ち受けるのは、パスワードのない簡易ログインを、
同じネットワークのほかの機器から開けないようにするため。
"""

from __future__ import annotations

import uvicorn


def main() -> None:
    uvicorn.run("app.main:create_app", factory=True, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
