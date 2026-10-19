def test_segments_every_question(synth, synth_key):
    pages, seg, _ = synth_key
    assert len(seg.questions) == len(synth.questions)
    assert seg.questions[0].parts[0].page >= 1  # page 1 is the cover
    for q in seg.questions:
        assert "answer not kn" in q.text.lower()
        assert q.text.lower().count("answer not kn") == 1


def test_margin_numbers_mostly_agree(synth_key):
    _, seg, _ = synth_key
    read = [q for q in seg.questions if q.margin_number is not None]
    assert len(read) >= len(seg.questions) // 2
    assert sum(q.margin_number == q.index for q in read) >= 0.9 * len(read)


def test_footer_is_not_question_text(synth_key):
    _, seg, _ = synth_key
    assert not any("turn over" in q.text.lower() for q in seg.questions)
