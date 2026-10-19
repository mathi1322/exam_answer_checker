from app.response_parser import IonResponseSheet, detect_format, numbering_problems


def test_parse_counts_ids_and_choices(synth, synth_sheet):
    by_q = synth_sheet.by_qnum()
    assert len(synth_sheet.questions) == len(synth.questions)
    assert numbering_problems(synth_sheet) == ([], [])
    for sq in synth.questions:
        q = by_q[sq.resp_qnum]
        assert q.question_id == sq.question_id
        assert q.chosen == sq.chosen
        assert q.crop_path and q.image_page >= 0


def test_not_answered(synth, synth_sheet):
    unanswered = [sq.resp_qnum for sq in synth.questions if sq.chosen is None]
    assert unanswered, "fixture should contain unanswered questions"
    by_q = synth_sheet.by_qnum()
    for n in unanswered:
        assert by_q[n].status == "Not Answered"
        assert by_q[n].chosen is None


def test_header_and_blocks(synth_sheet):
    assert synth_sheet.subject.startswith("Statistics")
    for q in synth_sheet.questions:
        assert len(q.parts) == 1
        part = q.parts[0]
        assert part.bottom > part.top
        # the block includes the question image
        assert part.top <= q.image_bbox[1] and q.image_bbox[3] <= part.bottom


def test_ocr_text_present(synth, synth_sheet):
    by_q = synth_sheet.by_qnum()
    sq = synth.questions[0]
    assert sq.stem.split()[1].lower() in by_q[sq.resp_qnum].ocr_text.lower()


def test_detect_rejects_key(synth):
    assert detect_format(synth.response_pdf) is not None
    assert detect_format(synth.key_pdf) is None
    assert not IonResponseSheet().detect(synth.key_pdf)
