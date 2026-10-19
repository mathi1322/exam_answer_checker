# User guide

This guide is for people who use the checker to score an exam. Running and maintaining the service is covered in
[OPERATIONS.md](OPERATIONS.md).

## What you need

| File | What it is | Where to get it |
|---|---|---|
| **Answer key** | The official question paper with the correct option of every question marked by a pen tick. Usually a scanned PDF. | Published by the exam body (for example, a TNPSC tentative or final key). |
| **Response sheet** | Your own digital response sheet. It lists every question with the option you chose. | Downloaded from the exam portal (for example, the TNPSC / TCS iON candidate login). |

The questions are in a different order in the two files. The checker matches them by their text, so you do not need
to reorder anything.

The checker supports the **TNPSC-style scanned answer key** and the **TCS iON digital response sheet**. Other layouts
are rejected with a message.

## Step 1: Upload

Open the checker in your browser (for example, <http://localhost:8000>) and choose the two files.

1. **Answer key**: the scanned key PDF.
2. **Response sheet**: your response sheet PDF.
3. **Scoring** (you can change all of these later on the results page):
   - **Marks per correct answer**: 1.5 by default.
   - **Marks deducted per wrong answer**: 0 by default (no negative marking).
   - **Option E, “Answer not known”**: either *neither right nor wrong* (the default, reported separately) or
     *count as wrong*.
   - **Unanswered questions**: either *neither right nor wrong* (the default) or *count as wrong*.
4. **Ask Claude (AI) about unclear ticks**: this box appears only if the administrator has enabled it. If you tick
   it, images of the answer-key questions whose tick could not be read are sent to Anthropic's API. Nothing from your
   response sheet is sent.

Press **Upload and check**.

The upload is rejected straight away if:

- a file is not a real PDF, is password-protected, or is over the size or page limit (50 MB and 300 pages by
  default);
- the response sheet is not in a supported format (the message also asks whether the two files were swapped);
- the response sheet's question numbers have gaps or duplicates.

## Step 2: Wait

A progress page shows each stage. A 200-question scanned key of about 120 pages takes a few minutes, mostly to read
the scan. You can leave the page open; it updates by itself.

Processing can still stop with a message if:

- **the question counts do not match**: the message shows both counts. The key is probably incomplete, or the files
  are for different papers.
- **no question blocks are found in the answer key**: it is probably the wrong file.

A yellow warning (not an error) appears if the subject on your response sheet does not appear on the key's cover
page. Check that the files belong together.

## Step 3: Review

If anything is uncertain, a review page opens before the results. Each flagged question shows:

- **left**: the answer-key question, with its tick;
- **right**: the response-sheet question the checker matched to it, and the option you chose;
- **the reasons it was flagged** (in yellow).

| Flag | Meaning | What to do |
|---|---|---|
| *Match: low score / small margin / best text match is Q.x* | The text match is uncertain. | Compare the two questions. If they differ, type the right response question number in **Matches response question**, or click one of the **Other likely matches**. |
| *Tick unclear* | The checker could not tell which option is ticked. | Look at the key image and choose the ticked letter in **Key answer**. |
| *Option order: reordered / suspect* | The options may be in a different order in the two documents. | Check the option texts. A *reordered* key answer has already been translated to your sheet's letters. A *suspect* one has not. |
| *Margin number reads N* | The question number printed in the key's margin did not match its position. | Check that the key image shows one complete question. This usually means the scan was misread and is harmless when the match looks right. |

**Changing a match swaps two rows.** If you set key Q10 to response Q.55 and Q.55 was matched to key Q20, then key
Q20 gets Q10's old question. Swapped rows are outlined so you can check them. Each response question is always used
exactly once.

Tick **Show all pairs** to look through every question, not only the flagged ones.

Press **Confirm and see results**. You can come back to the review from the results page and change things again.

If nothing is flagged, the review is skipped and the results open directly.

## Step 4: Results

The results page shows:

- **marks** out of the maximum;
- **cut-off count**: the number of correct answers;
- **correct**, **wrong**, **chose E** and **unanswered** counts;
- a table of every question in answer-key order, coloured green (right), red (wrong) or yellow (anything else).
  Tags show answers you set by hand (**you**), answers read by AI (**AI**) and matches you changed.

If some questions still have no key answer, a warning says how many. Those questions are counted as **Key unknown**
and score nothing until you set them on the review page.

**Change scoring** recalculates everything (including the downloads) with different marks or E / unanswered rules.
You do not need to upload again.

### Downloads

| File | Contents |
|---|---|
| **Reordered response sheet (PDF)** | A summary page with your score and a table of all questions. After it, every question block from your response sheet appears in **answer-key order**, still showing its original `Q.n`. A coloured bar above each block reads, for example, `Answer key Q1 = Response Q.167   Key: A   Chosen: A   CORRECT`. Green means correct, red wrong, amber E, grey unanswered. Use it to check the result side by side with the key. |
| **evaluation.csv** | One row per question: answer key Q, response sheet Q, Question ID, key answer, chosen option, result, match score, flagged. Opens in Excel or Google Sheets. |
| **results.json** | Everything, for technical users. See [API.md](API.md#resultsjson). |

## Step 5: Delete your data

Your response sheet contains personal details. Everything you upload, and every result, is deleted automatically
24 hours after upload (the administrator can change this). To delete sooner, press **Delete now** at the bottom of
the results page.

Anyone who has the job's link (`/jobs/…`) can open your results until they are deleted. Do not share the link unless
you mean to.

## How accurate is it?

- **Matching** compares the text of the questions. In the original hand-checked sample, all 200 matches were
  correct, and 32 of them were flagged for review because their text was similar or badly read. Maths-heavy
  questions are read worst and are flagged most often.
- **Ticks** are read from the image. A tick is accepted only when one option clearly stands out. Faint, very small
  or ambiguous ticks are left for you to decide on the review page rather than guessed.

Always spot-check a few questions in the reordered PDF against the key, especially before relying on a borderline
score.
