# Troubleshooting

This guide lists common problems by symptom: what they mean and what to do. "User" fixes can be done from the
browser. "Operator" fixes need access to the server or the code.

## Upload is rejected

| Message | Cause | Fix |
|---|---|---|
| "… is not a real PDF file. It may have been renamed from another format." | The file does not start with `%PDF`. It is an image, Word file or HTML page saved as `.pdf`. | User: download the PDF again from the source, or print to PDF. |
| "… is password-protected." | The PDF is encrypted. | User: open it in a PDF viewer and save or print an unlocked copy. |
| "… is larger than the 50 MB limit" / "… more than the 300-page limit" | The limits are reached. | Operator: raise `MAX_FILE_MB` / `MAX_PAGES`, and the proxy's body-size limit too. |
| "The response sheet is not in a supported format …" | No `Q.n` labels or Question ID / Chosen Option text in the first 4 pages. Often the two files are swapped. A scanned or printed-to-image response sheet also has no text layer. | User: check the file order. Download the original digital response sheet, not a scan or screenshot. |
| "The response sheet's question numbers are not complete (missing: …)" | Pages are missing, or the PDF is cut short. | User: download the full response sheet again. |

## Processing stops with an error

| Message | Cause | Fix |
|---|---|---|
| "The question counts do not match: the response sheet has X … but Y … in the answer key" | **Y < X:** the key is incomplete, or OCR could not read one or more "Answer not known" lines, so two questions merged. **Y > X:** "Answer not known" text appeared somewhere other than option E, or the files are for different papers. | User: check that the key is the full paper (count its pages). Operator: re-run with `RENDER_DPI=300`. To find where it went wrong, run `tools/debug_ticks.py`: it prints how many questions were segmented, and the per-question crops show where two questions merged. |
| "No question blocks could be detected in the answer key." | No "Answer not known" text anywhere. Wrong file, an unsupported paper format, or an unreadable scan. | User: check the file. Operator: see "Adding a new exam format" in [DEVELOPMENT.md](DEVELOPMENT.md#adding-a-new-exam-format). |
| "Could not find the question images in the response sheet." | The response sheet's questions are not embedded images (a different portal layout). | Operator: a new response-sheet format is needed. |
| "Something went wrong while processing the files." | An unexpected exception. | Operator: check the server log for `job <id> failed with <Type>` and the stack frames. |
| "Processing was interrupted by a server restart." | The server restarted while the job was queued or running. | User: upload again. |

## Results look wrong

| Symptom | Cause | Fix |
|---|---|---|
| A question is matched to the wrong response question | The texts are very similar (for example, two questions with the same stem and different numbers) or badly OCR'd (maths). It is usually flagged. | User: fix it on the review page; the swap keeps everything consistent. Operator: if many wrong matches go **unflagged**, raise `MATCH_MIN_SCORE` / `MATCH_MIN_MARGIN`. |
| Too many questions flagged for matching | The OCR is noisy, so the scores are low across the board. | Operator: check one key crop. If the scan is poor, try `RENDER_DPI=300`. Lower the match thresholds only after checking that the flagged pairs are actually correct. |
| A detected key answer is wrong (confident) | The tick detector was fooled. See the signals in `results.json` → `questions[k].tick`. | User: correct it on the review page (tick "Show all pairs"). Operator: tune following [TICK_DETECTION.md](TICK_DETECTION.md#tuning-on-a-real-key). |
| Many "Tick unclear" flags | Faint or unusual ticks, or labels placed in the wrong spot. | User: set them on the review page, or enable the AI fallback. Operator: run `tools/debug_ticks.py` and follow the tuning table. |
| "Key unknown" results | A tick was left uncertain and not set during review. | User: open "Back to review", set the key answers, confirm. |
| "Option order: suspect" flags | The option texts could not be compared reliably. | User: compare the two images. If the options really are in a different order, set the key answer in the response sheet's letters. |
| "Margin number reads N" | OCR read a different number in the key's margin. Usually a misread, which is harmless if the crop shows exactly one question. | User: check that the crop shows one whole question. If it shows two, the segmentation is off. Report it to the operator. |
| The subject warning appears | The response sheet's subject words were not found on the key's cover page OCR. | User: make sure both files are for the same paper. If they are, ignore it. |
| Score differs from an official one | Different marking rules. | User: use "Change scoring" (marks, negative marking, E and unanswered handling). |

## Server problems

| Symptom | Cause | Fix |
|---|---|---|
| `/healthz` returns 503 | `tesseract` or `pdftoppm` is not on the PATH. | Use the Docker image, or install `tesseract-ocr` and `poppler-utils`. |
| `TesseractError` … `eng.traineddata` in the logs | The English language data is missing. | Install `tesseract-ocr-eng`. |
| Jobs sit in "Waiting to start" | All `JOB_WORKERS` are busy with other jobs. | Wait, or raise `JOB_WORKERS` (CPU permitting). |
| Processing is very slow | Too few CPU cores, or `OCR_THREADS` set too low. | Raise `OCR_THREADS` to the core count. Keep `RENDER_DPI` at 200. |
| Disk keeps filling | The cleanup thread is not running, or the retention is too long. | Check the logs for `cleanup failed`. Lower `RETENTION_HOURS`. |
| `PermissionError` on `/data` after upgrading the image | The image now runs as the non-root user `app`, and the volume was created by an older image running as root. | Remove the volume, or run `docker run --rm -u root -v exam-data:/data exam-answer-checker chown -R app:app /data` once. |
| Running jobs restarted as errors after deploying | Expected: in-process jobs do not survive a restart. | Deploy when no job is processing. |
| Multiple uvicorn workers behave oddly (jobs marked interrupted) | The job store assumes a single process. | Run one process. Use `JOB_WORKERS` / `OCR_THREADS` for parallelism. |
| `docker compose` "unknown command" | The Compose plugin is not installed. | Install it, or use the plain `docker build` / `docker run` commands in [OPERATIONS.md](OPERATIONS.md#plain-docker). |
