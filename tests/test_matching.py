import random

from app import matching


def _noisy(text: str, rng: random.Random, rate: float) -> str:
    chars = list(text)
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789 .,;:()"
    for i in range(len(chars)):
        if rng.random() < rate:
            op = rng.random()
            if op < 0.4:
                chars[i] = rng.choice(alphabet)
            elif op < 0.7:
                chars[i] = ""
            else:
                chars[i] += rng.choice(alphabet)
    return "".join(chars)


def test_normalise_drops_tamil_junk_and_answer_not_known():
    assert matching.normalise("(E) Answer not known  xkcd bpmz 42 Mean") == "e 42 mean"


def test_recovers_shuffled_noisy_permutation(synth_sheet):
    rng = random.Random(11)
    texts = [q.ocr_text for q in synth_sheet.questions]
    qnums = [q.qnum for q in synth_sheet.questions]
    perm = list(range(len(texts)))
    rng.shuffle(perm)
    key_texts = [_noisy(texts[j], rng, rng.uniform(0.05, 0.15)) for j in perm]
    pairs, S = matching.match(key_texts, texts, qnums)
    assert S.shape == (len(texts), len(texts))
    assert [p.resp_qnum for p in pairs] == [qnums[j] for j in perm]


def test_matches_real_ocr(synth, synth_sheet, synth_key):
    _, seg, _ = synth_key
    pairs, _ = matching.match([q.text for q in seg.questions], [q.ocr_text for q in synth_sheet.questions],
                              [q.qnum for q in synth_sheet.questions])
    assert [p.resp_qnum for p in pairs] == [q.resp_qnum for q in synth.questions]


def test_flags_ambiguous_pairs():
    same = "the arithmetic mean of the sample"
    pairs, _ = matching.match([same, same, "unrelated question about tariffs"],
                              [same, "unrelated question about tariffs", same], [1, 2, 3])
    assert pairs[2].resp_qnum == 2 and not pairs[2].flagged
    pairs = pairs[:2]
    assert all(p.flagged for p in pairs)
    assert any("margin" in r for p in pairs for r in p.reasons)
