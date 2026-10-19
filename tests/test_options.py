from app import options


def test_same_order():
    k = "Which?\n(A) mean value (B) median value\n(C) mode value (D) range value\n(E) Answer not known"
    r = "Which?\nA. mean value\nB. median value\nC. mode value\nD. range value\nE. Answer not known"
    assert options.compare(k, r).status == "same"


def test_reordered_is_translated():
    k = "Q\n(A) inflation rate (B) fiscal deficit\n(C) bank reserve (D) trade balance\n(E) Answer not known"
    r = "Q\nA. trade balance\nB. bank reserve\nC. fiscal deficit\nD. inflation rate\nE. Answer not known"
    chk = options.compare(k, r)
    assert chk.status == "reordered"
    assert options.translate("A", chk) == "D" and options.translate("E", chk) == "E"


def test_missing_ticked_label_does_not_false_alarm():
    k = "Q\n(A) inflation rate (B) fiscal deficit\nbank reserve (D) trade balance\n(E) Answer not known"
    r = "Q\nA. inflation rate\nB. fiscal deficit\nC. bank reserve\nD. trade balance\nE. Answer not known"
    assert options.compare(k, r).status == "same"


def test_unreadable_is_unchecked():
    assert options.compare("garbage", "A. x").status == "unchecked"
