# 前の物語（比べの土台）

基本の RAG（BM25）と今の方式を比べたときの土台です。デモと動画の物語は `data/select/` にあり、ここは使いません。

| 場所 | 中身 |
|---|---|
| `story/` | 前の物語の、10年分の架空の会話、言明への分割、固める処理の AI の結果（JSON） |
| `plan.py` | 前の物語の予定 |
| `load.py` | 保存した JSON を AI を呼ばずに読み込む |
| `generate.py`、`consolidate_story.py` | 前の物語を作った道具 |
| `compare_rag.py` | BM25 と今の方式の比べ。問いは `eval/rag_compare_questions.json`、結果は `docs/verification/rag_compare.json` |
| `measure_reading.py` | 記録の大きさと読む量の測定 |

```
python -m eval.previous_story.compare_rag --help
python -m eval.previous_story.measure_reading --help
```

比べの数字は [docs/tools.md](../../docs/tools.md) にあります。前の物語で測った結果で、今の物語では測り直していません。
