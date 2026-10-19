from app import ticks


def _run(synth, synth_key):
    pages, seg, paths = synth_key
    imgs = {i: ticks.load_page_image(p) for i, p in enumerate(paths)}
    return [(sq, ticks.detect_tick(q, pages, imgs)) for sq, q in zip(synth.questions, seg.questions)]


def test_confident_detections_are_correct(synth, synth_key):
    results = _run(synth, synth_key)
    confident = [(sq, r) for sq, r in results if not r.uncertain]
    wrong = [(sq.key_index, sq.answer, r.answer) for sq, r in confident if r.answer != sq.answer]
    assert not wrong, f"confident but wrong: {wrong}"
    assert len(confident) >= 0.9 * len(results)


def test_handles_grid_column_and_faint(synth, synth_key):
    results = _run(synth, synth_key)
    for kind in ("grid", "column"):
        sub = [r for sq, r in results if (sq.grid if kind == "grid" else not sq.grid)]
        assert sub and sum(not r.uncertain for r in sub) >= 0.8 * len(sub)
    faint = [(sq, r) for sq, r in results if sq.faint]
    assert all(r.uncertain or r.answer == sq.answer for sq, r in faint)


def test_labels_located_near_truth(synth, synth_key):
    results = _run(synth, synth_key)
    for sq, r in results:
        assert set(r.labels_found) >= set("ABCDE"), (sq.key_index, r.note)


def test_decide():
    assert ticks._decide({"A": 100, "B": 400, "C": 110, "D": 95, "E": 105}, 1.6, 1.3) == "B"
    assert ticks._decide({"A": 100, "B": 400, "C": 390, "D": 95, "E": 105}, 1.6, 1.3) is None
    assert ticks._decide({"A": 100, "B": 120, "C": 110, "D": 95, "E": 105}, 1.6, 1.3) is None
