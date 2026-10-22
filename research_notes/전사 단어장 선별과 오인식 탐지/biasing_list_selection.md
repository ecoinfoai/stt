# Selecting a small biasing list from a large glossary for ASR (two-pass, hypothesis-driven)

Source-status tags used below: **[PR]** peer-reviewed venue confirmed on the source/venue page; **[PP]** preprint or venue not verified in this research; **[PRAC]** practitioner/vendor documentation (not peer-reviewed); **[CODE]** source code read directly (faster-whisper commit `a52b026`, 2026-10-10). "Det." means deterministic. Where a determinism or cost judgment is mine and not stated by the authors, it is marked *(inferred)*.

Setting assumed throughout: pass 1 is faster-whisper large-v3 with no hints. A per-professor glossary of thousands of terms (term list only) is matched against pass 1. Pass 2 re-decodes the same audio with at most 223 prompt tokens (about 40 short Korean terms). Hardware is one RTX 2070 8 GB.

---

## Q1. Two-pass / hypothesis-driven biasing: which systems use first-pass output (1-best, N-best, lattice, confidence) to retrieve candidates, and how do they score them?

### Takeaway
Hypothesis-driven selection is a well-established pattern. A cheap first pass (streaming phone posteriors, CTC greedy text, or a full ASR 1-best/N-best) is matched against the large list, a short list survives (often fewer than 10 entries per utterance, at most about 100), and a second decoder or corrector uses it. Filtering consistently beats passing the full list. In the clearest case, a 6,253-entry list gave **no** gain (−0.3% CERR). Filtering it to an average of 3.7 entries gave **20.3% CERR**, about 60% of the oracle-list topline. Scoring ranges from deterministic, training-free string and phone matching (normalized edit distance, n-gram indexes, posterior dynamic programming) to learned embeddings and LLM re-ranking.

### Cited Findings

**A. Phone-posterior two-stage filter (Yang et al., NWPU/Tencent) [PP]**
- A streaming causal encoder emits phone posteriors. A sliding window over them filters the predefined list, and the surviving list feeds an attention-based context-bias module in the non-streaming second pass. Phones were chosen "to match more possible homophonic contextual word candidates" — [Yang et al., arXiv:2301.06735](https://arxiv.org/abs/2301.06735)
- Stage 1, **Posterior Sum Confidence (PSC)**: "the sum of the maximum posterior probabilities of all phones belonging to the contextual word appeared in the sliding window," normalized by sequence length, ignoring order. It removes most irrelevant words — [arXiv:2301.06735](https://arxiv.org/abs/2301.06735)
- Stage 2, **Sequence Order Confidence (SOC)**: a monotonic dynamic program over the posterior matrix p[t,f] for the word's phone sequence U. The recurrence is dp[i,j] = max(dp[i−1,j−1] + p[j,U_i], dp[i,j−1]) and the score is S_SOC = dp[−1][−1]/len(U), cut by a threshold — [arXiv:2301.06735](https://arxiv.org/abs/2301.06735)
- Music Search (6,253-entry list): baseline CER 2.51%; full list 2.52% (−0.3% CERR); PSC 2.07% (17.5%); PSC+SOC 2.00% (20.3%); oracle list 1.66% (33.8%). Contacts (972-entry list): baseline 10.22%; full list 7.13% (30.2%); PSC+SOC 6.01% (41.1%); oracle 4.81% (52.9%) — [arXiv:2301.06735](https://arxiv.org/abs/2301.06735)
- Filter quality is reported as Entity Recall Rate (ERR) and Average List Size (ALS). Music: PSC gives ERR 96.04% at ALS 17; adding SOC gives ERR 94.36% at ALS 3.7. Contacts: PSC gives 92.88% at 12.7; adding SOC gives 91.19% at 2.8 — [arXiv:2301.06735](https://arxiv.org/abs/2301.06735)
- Feeding the *other* domain's list (mismatched list, "UD") did not hurt: 3.6% and 5.7% CERR versus baseline — [arXiv:2301.06735](https://arxiv.org/abs/2301.06735)
- Cost: single-thread CPU real-time factor (RTF) was 4.670 with the full Music list and 0.149 after filtering — [arXiv:2301.06735](https://arxiv.org/abs/2301.06735)

**B. CTC-assisted LLM-based contextual ASR (2-gram index + edit distance) [PP]**
- A coarse CTC greedy hypothesis (WavLM-Large CTC) is used to filter a large hotword list. The surviving hotwords go into the LLM-ASR prompt — [Yang et al., arXiv:2411.06437](https://arxiv.org/abs/2411.06437)
- **F1**: build a character 2-gram index of the biasing list (e.g., "bob" yields "bo" and "ob"), retrieve candidates for the hypothesis, compute edit-distance similarity, and keep candidates with similarity > 0.95 or the top 5. **F2**: first delete "common words" (the 5,000 most frequent training-corpus words) from the hypothesis. **F3** (the proposed variant): "match every word in the inference sentence with the most similar hotword from the biasing list" — [arXiv:2411.06437 (HTML)](https://arxiv.org/html/2411.06437v1)
- With 100-word lists, WER/B-WER on test-clean: F1 1.39/4.61, F2 1.40/4.85, F3 1.27/3.67. On test-other: F1 3.19/10.12, F2 2.92/9.78, F3 2.72/8.02 — [arXiv:2411.06437](https://arxiv.org/html/2411.06437v1)

**C. Microsoft contextual spelling correction (CSC) pre-selection rankers [PP; Microsoft Research]**
- A "context pre-selection mechanism" picks the top K_f of K_r raw phrases using a **relevance ranker** (ASR hypothesis versus phrase) and a **preference ranker** (prior knowledge, "examples like the call frequency of context phrases in the target domain") — [Wang et al., arXiv:2203.00888](https://arxiv.org/abs/2203.00888)
- Relevance formula: **W_r^j = − min_i edit_distance(c_j, e_i) / len(c_j)**. Here e_i is the hypothesis segment of the same length as phrase c_j, starting at the i-th word; at the end of the hypothesis the remaining characters are used. Final selection: **c_1..c_Kf = argtopK_j(α_p·W_p^j + (1−α_p)·W_r^j)** — [arXiv:2203.00888](https://arxiv.org/abs/2203.00888)
- K_f = 100 at inference, "consistent with training." Up to 4 ASR candidates (N-best) are corrected for RNN-T, 1 for T-T. Correction fires when any candidate passes an NER/pattern detector, or when a confidence threshold s_o is met (s* = mean_i max_j s_ij) — [arXiv:2203.00888](https://arxiv.org/abs/2203.00888)
- Reported up to 51% relative WER reduction over the ASR system — [Microsoft Research page](https://www.microsoft.com/en-us/research/?p=985656); [arXiv:2203.00888](https://arxiv.org/abs/2203.00888)

**D. Retrieval-augmented correction of entity errors (Apple / Capital One et al.) [PP]**
- Queries are generated from the "possibly errorful" ASR 1-best text, matched against a vector database of entities, and the retrieved entities plus the hypothesis go to an ASR-error-adapted LLM — [Pusateri et al., arXiv:2409.06062](https://arxiv.org/abs/2409.06062); listed as a publication on the [Capital One publication page](https://www.capitalone.com/tech/publications/retrieval-augmented-correction/)
- Three query generators: all n-grams up to N_max (simple, over-generates), hand-written templates (regex), and a trained named-entity (NE) tagger — [arXiv:2409.06062 (HTML)](https://arxiv.org/html/2409.06062v1)
- Index types compared: BM25, T5 semantic embeddings, and **Acoustic Neighbor Embeddings (ANE)** built from orthography or pronunciation. ANE recall@1/5/10 was 84.8/91.6/92.8% against T5's 80.7/86.9/88.4%. The settings were Euclidean distance threshold D_max = 1.0 and top-k of 1 or 5; top-5 gave little over top-1 — [arXiv:2409.06062](https://arxiv.org/html/2409.06062v1)
- 33–39% relative WER reduction on synthetic rare-music-entity sets, with no regression on STOP (OpenLLaMA-7B, LoRA rank 4) — [arXiv:2409.06062](https://arxiv.org/abs/2409.06062)

**E. DeRAGEC — denoising retrieved named-entity candidates [PP]**
- NE spans are taken from the top hypothesis and turned into a **phonetic** query. The top-10 phonetically similar entities are retrieved from a 3,003,462-entity database, and the 5-best hypotheses are also passed. Phonetic similarity uses articulatory features (Epitran + Panphon) — [arXiv:2506.07510](https://arxiv.org/html/2506.07510v1)
- The method is training-free (in-context learning). Denoising raised candidate precision from 0.056 to 0.139 at recall 0.839 (upper bound 0.841). WER (CommonVoice, Llama-3.1) went from 7.7% (ASR only) to 6.5% (plain retrieval-augmented correction) to 6.0% (DeRAGEC). The authors state that "irrelevant or weakly related NEs often introduce noise, thereby undermining correction performance" — [arXiv:2506.07510](https://arxiv.org/html/2506.07510v1)

**F. RECOVER — multi-hypothesis entity correction with explicit retrieval score [PP]**
- Each catalog phrase is scored against *all* ASR hypotheses: **1.0 × exact-token matches + 1.2 × fuzzy similarity** (normalized Levenshtein, length within ±3 characters) **+ 0.6 × phonetic-prefix match** (phonetic keys share a 5-character prefix). Top K = 200 are kept from catalogs of 446 to 6,198 phrases — [arXiv:2603.16411](https://arxiv.org/html/2603.16411v1)
- Guardrails on LLM edits: the replacement must be in the list, case-only edits are rejected, a Levenshtein-similarity threshold applies, and spans are verified. Results: 8–46% relative entity-phrase WER reduction, up to +22 pp recall, and substitutions make up 63–93% of the fixes. A merge-style ensemble (ROVER) raised WER by 15+ points on noisy domains — [arXiv:2603.16411](https://arxiv.org/html/2603.16411v1)

**G. Whisper-specific two-pass and pre-decoder selection**
- *Whisper: Courtside Edition* [PP]: Whisper's first transcript goes to LLM agents (domain identification, NER, jargon detection) that write a compact prompt for a second Whisper pass. No term catalog is mentioned. On 421 NBA-commentary segments, WER fell from 0.217 to 0.180 (17.0% relative, p < 0.001); 40.1% of segments improved and 7.1% got worse — [Ron et al., arXiv:2602.18966](https://arxiv.org/abs/2602.18966)
- *CB-Whisper* [PR, LREC-COLING 2024]: entities are selected **from audio**, not text. Each entity is synthesized with TTS (edge-tts) and encoded once with the Whisper encoder. At test time a cosine-similarity matrix between utterance and entity features goes to a 4-layer, 0.2M-parameter CNN that looks for the diagonal pattern. Detected entities become the prompt — [Li et al., LREC-COLING 2024](https://aclanthology.org/2024.lrec-main.262); [arXiv:2309.09552](https://arxiv.org/abs/2309.09552)
- CB-Whisper used as a plug-in to frozen Whisper-large-v2 with a "spoken-form" prompt ("The topic of today's speech is, ah, entity 1, entity 2… Okay, then I'll continue."): MER 4.4→2.9% and 5.6→3.6%, entity recall 87.3→97.7% and 79.9→96.9%. The authors note that "experiments with the Whisper-large-v3 model … did not outperform the v2 model" — [arXiv:2309.09552](https://arxiv.org/abs/2309.09552)
- *Keyword-Guided Adaptation (KG-Whisper)* [PP]: AdaKWS on the Whisper encoder predicts which keywords from the list are present. Detected keywords are placed between ⟨start_of_prev⟩ and ⟨start_of_transcript⟩. On Voxpopuli, KG-Whisper-PT (a learned prompt prefix of about 15K parameters) gave WER 11.10% against 13.39% for Whisper, and keyword F1 95.25% against 81.77% — [arXiv:2406.02649](https://arxiv.org/html/2406.02649v1)

**H. Older first-pass-driven retrieval and correction**
- Amazon AWS AI kNN slot correction [PP]: keys are joint phoneme and text representations of the first-pass hypothesis (phonemes from the ASR lexicon); values are entities. Retrieval uses k = 10 and λ = 0.5 with no extra training. Gains were 7.4% relative on rare-entity WER and 9.8% on OOV; phonetically similar but wrong retrievals occur for unseen entities — [Bekal et al., arXiv:2109.05092](https://arxiv.org/abs/2109.05092)
- Augmented Transformer that "attends to phoneme information in addition to word-level information" to correct mistranscribed entities: 48.08% entity-error-rate reduction — [Wang et al., Interspeech 2020](https://www.isca-archive.org/interspeech_2020/wang20p_interspeech.html) [PR]
- LLM generative error correction for rare words using N-best hypotheses plus phonetic context to reduce over-correction (English and Japanese) — [Interspeech 2025](https://www.isca-archive.org/interspeech_2025/yamashita25_interspeech.html); [arXiv:2505.17410](https://arxiv.org/abs/2505.17410) [PR; abstract-level only]
- Classic spoken-term detection uses **proxy keywords**. For an out-of-vocabulary (OOV) keyword absent from lattices, acoustically similar in-vocabulary words or phone sequences are generated with WFSTs that include a phone confusion matrix. Confusion information gave up to 40% improvement in ATWV — [Chen et al., ASRU 2013](https://www.clsp.jhu.edu/wp-content/uploads/2015/11/asru2013_proxy_keyword.pdf) [PR]

### Inferences
- Our pipeline is a direct instance of "first-pass text → retrieve from list → re-decode with filtered list." The closest training-free analogues that need only pass-1 text and the glossary are CTC-assisted F3 (B), the Microsoft relevance+preference formula (C) and RECOVER's additive score (F). All run on CPU and leave the 8 GB GPU free for Whisper.
- The '간념'/'폐혈증'/'cnv' problem is the same problem proxy keywords (H) and SpellMapper (Q2) address: the right term is absent from the text, so retrieval must work on *sub-word similarity* (character or phone), not exact lookup.
- For Korean, edit distance on whole syllables is too coarse for 2–3-syllable terms. Decomposed Hangul (jamo) or a pronounced form (G2P) is the natural analogue of the phone-level matching in A, D and E. My own worked example: syllable distance makes '간념' equally far from '간염' and '간암' (1 of 2 syllables, normalized 0.5). Jamo distance gives ㄱㅏㄴㄴㅕㅁ→ㄱㅏㄴㅇㅕㅁ = 1/6 against ㄱㅏㄴㅇㅏㅁ = 2/6, so the tie breaks correctly. '폐혈증'→'패혈증' is one vowel jamo (ㅖ→ㅐ); 'cnv'→'CMV' is one character after case folding. Not validated on our data.
- First-pass confidence is available for free. faster-whisper `Word` objects carry `probability` [CODE: [transcribe.py L32–36](https://github.com/SYSTRAN/faster-whisper/blob/a52b026ac844c02117865e61968547639b60a6f8/faster_whisper/transcribe.py#L32-L36)], and segments carry `avg_logprob`, `compression_ratio` and `no_speech_prob`. By analogy with the confidence gating in C and with A, low-probability spans are the natural places to query. This is an extrapolation; no paper found tests Whisper word probabilities for glossary retrieval.

### Gaps
- No peer-reviewed paper found that does exactly: Whisper pass 1 with no hints → text-based glossary retrieval → Whisper pass 2 prompted with the filtered list, with measured gains. The closest are Courtside (LLM prompts, no catalog, preprint) and CB-Whisper/KG-Whisper (audio-side keyword spotting, need training).
- No Korean study of hypothesis-driven glossary retrieval was found. A 2007 Korean journal paper on phoneme-similarity post-correction ([KoreaScience](https://www.koreascience.or.kr/article/JAKO200709906244055.page)) was seen only as a search snippet.
- Full text of the Courtside paper (prompt length, hallucination behaviour) was not available.

---

## Q2. Retrieval methods for large catalogs (phonetic, embedding/ANN, keyword spotting, filtering stages)

### Takeaway
Four families exist:
- **(a) Text matching against the first-pass hypothesis**: n-gram index, edit distance, learned confusion n-grams, BM25.
- **(b) Phonetic matching**: phone posteriors, phone strings, articulatory features, proxy words via a phone confusion matrix.
- **(c) Learned embedding retrieval with ANN or top-K attention**: Acoustic Neighbor Embeddings, CLAP-style speech–bias embeddings, Deferred NAM.
- **(d) Audio-side keyword spotting**: the CTC word spotter, Whisper-encoder KWS using TTS exemplars.

(a) and (b) are training-free and deterministic. (c) and (d) need training, except the CTC word spotter, which only needs a CTC head. Large-catalog systems show that retrieval scales to 20K–200K entries with 99%+ pruning, at the price of a few points of B-WER.

### Cited Findings

#### Method inventory

| # | Method (source) | Inputs | Training needed | Det. | Scoring / selection rule | Reported result | Cost |
|---|---|---|---|---|---|---|---|
| 1 | PSC+SOC two-stage filter — [arXiv:2301.06735](https://arxiv.org/abs/2301.06735) [PP] | streaming phone posteriors, sliding window | ASR must emit phones; filter itself is training-free | yes *(inferred)* | PSC = Σ max phone posterior / len; SOC = monotonic DP / len(U); thresholds | 6,253 → 3.7 avg, ERR 94.4%, 20.3% CERR | RTF 4.67 → 0.15 (CPU) |
| 2 | CTC-assisted F3 — [arXiv:2411.06437](https://arxiv.org/html/2411.06437v1) [PP] | CTC greedy text | filter training-free (LLM-ASR is trained) | yes *(inferred)* | char 2-gram index → drop top-5k common words → each hypothesis word gets its most similar hotword (edit distance) | B-WER 3.67% (N=100) → 4.41% (N=2000), clean | negligible *(inferred)* |
| 3 | CSC rRanker/pRanker — [arXiv:2203.00888](https://arxiv.org/abs/2203.00888) [PP] | ASR text (up to 4-best) + prior weight | rankers training-free; corrector trained | yes | W_r = −min_i ED(c_j, e_i)/len(c_j); top-K_f of α·W_p + (1−α)·W_r; K_f = 100 | system up to 51% relative WERR | edit distance per phrase × position |
| 4 | SpellMapper retrieval — [Interspeech 2023](https://www.isca-archive.org/interspeech_2023/antonova23_interspeech.html) [PR] | ASR text fragments (10–15 words) | offline confusion n-gram table (1.9M mappings from a TTS→ASR corpus) | yes *(inferred)* | index user phrases by their n-grams **and misspelled variants**; rank by hit count, then coverage; top 10 | top-10 recall 74–90%; WER −6.4 to −21.4% relative | avoids quadratic edit distance |
| 5 | Retrieval-augmented correction with ANE — [arXiv:2409.06062](https://arxiv.org/html/2409.06062v1) [PP] | ASR 1-best → n-gram/template/NE queries | ANE embedding model and LLM (LoRA) trained | retrieval yes | Euclidean ≤ 1.0, top-1/5 | ANE recall@10 92.8% vs T5 88.4%; −33 to −39% WER | vector DB + 7B LLM |
| 6 | DeRAGEC — [arXiv:2506.07510](https://arxiv.org/html/2506.07510v1) [PP] | NE spans from 1-best + 5-best | none (ICL) | no (LLM) | articulatory-feature phonetic similarity, top 10 of 3M; LLM denoising | precision 0.056 → 0.139; WER 7.7 → 6.0 | LLM calls |
| 7 | RECOVER — [arXiv:2603.16411](https://arxiv.org/html/2603.16411v1) [PP] | multiple hypotheses | none | retrieval yes; LLM no | 1.0·exact + 1.2·fuzzy + 0.6·phonetic prefix; K = 200 | entity-phrase WER −8 to −46% | LLM calls |
| 8 | CTC Word Spotter (NeMo) — [arXiv:2406.07096](https://arxiv.org/html/2406.07096v1) [PP] | CTC log-probabilities | none (needs CTC or hybrid model) | yes | context graph (trie × CTC topology), beam; ctc_w = 0.5, c_bw = 3.0, blank threshold log 0.8, beam 7.0; accept only if it beats the greedy alignment score | F = 0.87 at 100 words, slight drop at 1,000; 15 s vs 179 s (pyctcdecode) | fast |
| 9 | CB-Whisper OV-KWS — [LREC-COLING 2024](https://aclanthology.org/2024.lrec-main.262) [PR] | audio (Whisper encoder) + TTS audio per entity | CNN classifier (0.2M), optional Whisper fine-tune | yes *(inferred)* | cosine-similarity matrix → binary CNN per entity | large-v2 MER 4.4 → 2.9, recall 87.3 → 97.7 | linear in #entities; TTS offline |
| 10 | KG-Whisper / AdaKWS — [arXiv:2406.02649](https://arxiv.org/html/2406.02649v1) [PP] | audio + keyword list | yes (decoder or 15K-param prefix) | yes *(inferred)* | binary presence vector over the list | Voxpopuli WER 13.39 → 11.10 | small |
| 11 | Deferred NAM — [arXiv:2404.10180](https://arxiv.org/html/2404.10180v2) [PR, NAACL 2024 Industry] | audio + phrase embeddings | end-to-end + phrase/wordpiece CE losses | yes | mean-max-pooled attention logits, top k = 32 | recall@32 99.7%; up to 37.5% relative WER | 20K phrases in 32.3 ms |
| 12 | BR-ASR — [Interspeech 2025](https://www.isca-archive.org/interspeech_2025/gong25_interspeech.html) [PR] | audio + bias encoder (text or TTS) | contrastive training + homophone curriculum | yes (FAISS) | dot product, top-k 10–50 | 2K list: B-WER 2.8/7.1; 200K list: +2.9 abs B-WER at 99.99% pruning | 20 ms/query (GPU) |
| 13 | Retrieve and Copy (Amazon) — [arXiv:2311.08402](https://arxiv.org/abs/2311.08402) [PR, EMNLP 2023] | audio encoder | yes | yes *(inferred)* | retrieval from encoder (abstract-level) | catalogs up to 20K; up to 6% WERR; 20% faster per frame | — |
| 14 | kNN slot correction — [arXiv:2109.05092](https://arxiv.org/abs/2109.05092) [PP] | 1-best + phonemes | none (datastore only) | yes | k = 10 NN, λ = 0.5 | rare-entity WER −7.4% relative | 1–5 GB datastore |
| 15 | Proxy keywords — [ASRU 2013](https://www.clsp.jhu.edu/wp-content/uploads/2015/11/asru2013_proxy_keyword.pdf) [PR] | lattices + phone confusion matrix | none beyond ASR | yes | WFST proxy generation | up to 40% ATWV gain | — |
| 16 | Alternate spelling prediction (Rev) — [Interspeech 2022](https://www.isca-archive.org/interspeech_2022/fox22_interspeech.html) [PR] | bias term list (offline) | 13.6M-param seq2seq | yes *(inferred)* | expands the bias list with predicted ASR misspellings | rare-word recall +34.7% relative, OOV +97.2% relative | small |
| 17 | Courtside (LLM prompts) — [arXiv:2602.18966](https://arxiv.org/abs/2602.18966) [PP] | Whisper 1-best | none | no | LLM agents write the prompt; no catalog | WER 0.217 → 0.180 | 2 Whisper passes + LLM |

#### Additional details
- SpellMapper's motivation: edit-distance retrieval is "slow, non-trainable, and may have low recall as it relies only on common letters." Example: "lookez" and "lucas" share one letter, but learned mappings ("luc" → "luke", "lok", "lus") connect them — [arXiv:2306.02317](https://arxiv.org/abs/2306.02317); [ar5iv](https://ar5iv.labs.arxiv.org/html/2306.02317). Mappings were mined from 4.5M Wikipedia headings synthesized with TTS, recognized by a Conformer-CTC at about 80% WER, and aligned with GIZA++ — [ar5iv](https://ar5iv.labs.arxiv.org/html/2306.02317). Final precision was only 77–87%, which the authors call "many false positives" — [ar5iv](https://ar5iv.labs.arxiv.org/html/2306.02317). Implemented in NeMo — [NeMo docs](https://docs.nvidia.com/deeplearning/nemo/user-guide/docs/en/main/nlp/spellchecking_asr_customization.html) [PRAC]
- CTC-WS false-accept example: "the actual word 'cloud' can be spotted as 'cuda' if 'cloud' is not presented in the context graph." It is mitigated by comparing the candidate score with the greedy word's score — [arXiv:2406.07096](https://arxiv.org/html/2406.07096v1)
- Deferred NAM moves the "lightweight phrase selection pass" *before* context encoding, giving up to 16.1× speed-up and 20K phrases under 33 ms pre-decoding delay — [arXiv:2404.10180](https://arxiv.org/html/2404.10180v1)
- BR-ASR offers two bias encodings: *AcousticBias* (TTS → speech encoder) and *TextualBias* (LLM features, which avoids TTS domain mismatch). Retrieved candidates are fed to ASR systems "without fine-tuning" — [arXiv:2505.19179](https://arxiv.org/html/2505.19179v1)
- Lecture-domain precedent: SlideSpeech extracts keywords from slide OCR with KeyBERT and feeds them to a contextual CTC/AED biasing model — [arXiv:2309.05396](https://arxiv.org/abs/2309.05396) (ICASSP 2024 per [Duke PDF](https://sites.duke.edu/dkusmiip/files/2024/03/icassp24_haoxu_slidespeech.pdf)). SAP² prunes long slide context with speech-driven attention pooling, giving 41.1% B-WER reduction on SlideSpeech — [arXiv:2511.11139](https://arxiv.org/abs/2511.11139) [PP]
- Hotword retrieval for LLM-ASR combining a contrastive retriever (GLCLAP) with "fuzzy matching," then GRPO fine-tuning — [arXiv:2512.21828](https://arxiv.org/abs/2512.21828) [PP; abstract only]

### Inferences
- faster-whisper large-v3 exposes no CTC head, so CTC-WS (#8) needs an extra Korean CTC model. Such a model could double as an independent acoustic check of candidates, but that is extra GPU memory on an 8 GB card. Text-side families (#2, #3, #4, #7, #15) fit the constraint best.
- SpellMapper (#4) and proxy keywords (#15) point to a Korean-specific, copyright-safe enhancement. Synthesize each glossary term with a Korean TTS, transcribe it with the *same* faster-whisper model, and store the observed misrecognitions ('간념', 'cnv', …) as extra index keys for that term. This uses only the term list and is per-professor by construction. No published Korean result exists.
- Audio-side KWS (#9, #10) is the only family that can recover terms whose pass-1 string is unrecognizably far from the term. It needs training, which is out of scope for a training-free first version.

### Gaps
- Full texts of Retrieve-and-Copy (#13) and the GLCLAP hotword retriever were not read; their retrieval details are abstract-level.
- No study was found that compares jamo-level versus syllable-level versus G2P-level matching for Korean term retrieval.
- Google's production "large catalog" contextual biasing papers beyond Deferred NAM, and Amazon Alexa catalog retrieval beyond #13/#14, were not examined in depth.

---

## Q3. Scoring/ranking criteria and the list-size trade-off (relevance vs distractors)

### Takeaway
Candidates are ranked by:
- **similarity to the hypothesis**: normalized edit distance, n-gram hits plus coverage, posterior sum or order-DP score, embedding distance;
- optionally a **prior** (domain frequency);
- **common-word exclusion**;
- **confidence or threshold gates**.

Every quantitative source shows accuracy falling as distractors grow, and plain *prompting* is the most fragile channel: speech-LLM B-WER degraded 39–570%, and Whisper with a naive term list raised MER. Effective lists after filtering are small (about 3–30 per utterance; at most 100 in trained correctors). Vendor guidance converges on "20–50 terms."

### Cited Findings
- **Distractor effect, trained deep biasing**: B-WER rose from 5.7% at N=100 distractors to 7.3% at N=2000 (test-clean). The model "does not work as well with biasing lists that are much larger than what is seen during training" — [Le et al., Interspeech 2021, arXiv:2104.02194](https://ar5iv.labs.arxiv.org/html/2104.02194) [PR]
- **Distractor effect, filtered LLM-ASR**: B-WER 3.67/3.92/4.16/4.41% for N = 100/500/1000/2000 (test-clean) and 8.02→10.02% (test-other), with U-WER "largely stable" — [arXiv:2411.06437](https://arxiv.org/html/2411.06437v1)
- **Full versus filtered**: −0.3% versus +20.3% CERR (6,253 list) and 30.2% versus 41.1% (972 list) — [arXiv:2301.06735](https://arxiv.org/abs/2301.06735)
- **Raw list size K_r versus filtered size K_f** (Microsoft CSC): "WER increases with K_r in a concave curve shape … raw context list size does not influence the model performance much when it's large enough" (K_f fixed at 100) — [arXiv:2203.00888](https://arxiv.org/abs/2203.00888)
- **Speech LLM versus dedicated biasing** (N = 0/10/100/250 distractors): a Whisper-based method's Earnings-21 B-WER went 9.79→11.23% and LibriSpeech 4.60→4.71%. Qwen3-Omni went 13.12→18.44%. Speech-LLM degradation was 39–570% relative, and the models were "sensitive with respect to the order in which the words … are placed in the prompt," so the authors sorted words alphabetically. Conclusion: dedicated biasing "remain[s] the more robust choice when a clean word list can be supplied," while speech LLMs need "an auxiliary filtering stage" — [Huber & Waibel, arXiv:2608.05759](https://arxiv.org/html/2608.05759) [PP]
- **Whisper prompt format matters** (CB-Whisper, predicted entities used as prompt). Whisper-medium with a *naive* comma list: MER 4.8→6.8% (Internal-1) and 6.7→14.6% (Internal-2). With a *spoken-form* prompt: 4.0% and 4.8%. The cause: "the Whisper model tended to omit filler words and disfluencies if the prompt was in a well-formatted text form" — [arXiv:2309.09552](https://arxiv.org/abs/2309.09552)
- **Oracle prompt ceiling for Whisper**: Whisper-small prompted with ground-truth entities went from MER 16.7→13.3% and entity recall 6.3→81.6% (Aishell-dev). KWS-predicted entities after fine-tuning reached recall 88.4%, beating the oracle prompt on the base model — [arXiv:2309.09552](https://arxiv.org/abs/2309.09552)
- **Hard (similar) distractors are the dangerous ones**:
  - Training with "phonetically similar phrases as negative examples" gave up to 53.1% relative WER improvement — [Alon et al., arXiv:1810.12170](https://arxiv.org/abs/1810.12170) [PP here]
  - ANN-mined similar phrases gave up to 7% relative WER reduction on the contextual portion — [Bleeker et al., Interspeech 2023](https://www.isca-archive.org/interspeech_2023/bleeker23_interspeech.html) [PR]
  - BR-ASR adds a homophone curriculum — [arXiv:2505.19179](https://arxiv.org/html/2505.19179v1)
- **Retrieval depth versus recall**: top-10 recall of 74–90% (SpellMapper); recall@1/5/10 of 84.8/91.6/92.8% (ANE); Deferred NAM recall@1/5/32 of 93.4/98.9/99.7%. Pusateri found top-5 gave little over top-1 — [ar5iv 2306.02317](https://ar5iv.labs.arxiv.org/html/2306.02317); [arXiv:2409.06062](https://arxiv.org/html/2409.06062v1); [arXiv:2404.10180](https://arxiv.org/html/2404.10180v2)
- **Vendor guidance [PRAC]**:
  - Deepgram keyterms are capped at 500 tokens per request: "Stay well under the 500 token limit; focus on the most important 20-50 terms." It warns against "excessive keyterms" and generic words that are "rarely misrecognized" — [Deepgram docs](https://developers.deepgram.com/docs/keyterm)
  - Google: boost "can also increase the likelihood of false positives"; the practical maximum boost is 20 — [Google Cloud docs](https://docs.cloud.google.com/speech-to-text/docs/boost). Phrase limits (1,200 per PhraseSet, 5,000 per request) were read from a search-result excerpt of the [quotas page](https://docs.cloud.google.com/speech-to-text/docs/quotas) and not re-fetched.
  - AssemblyAI streaming keyterms: up to 100 per session, 50 characters each — [Vapi docs](https://docs.vapi.ai/customization/custom-keywords)
- **Whisper prompt mechanics [PRAC/CODE]**:
  - OpenAI: "If the prompt is longer than 224 tokens, only the final 224 tokens of the prompt will be considered," and "The prompt will not override the model's comprehension of the audio" — [OpenAI Cookbook](https://developers.openai.com/cookbook/examples/whisper_prompting_guide)
  - faster-whisper `get_prompt`: `hotwords` are tokenized and, if ≥ 224 tokens, cut to the **first** `max_length // 2 − 1` = 223 tokens. Previous tokens are cut to the **last** 223. Hotwords are ignored when `prefix` is set — [CODE L1547–1555](https://github.com/SYSTRAN/faster-whisper/blob/a52b026ac844c02117865e61968547639b60a6f8/faster_whisper/transcribe.py#L1547-L1555)
  - `initial_prompt` is appended once to `all_tokens` at the start [CODE L1148–1154](https://github.com/SYSTRAN/faster-whisper/blob/a52b026ac844c02117865e61968547639b60a6f8/faster_whisper/transcribe.py#L1148-L1154). `prompt_reset_since = len(all_tokens)` runs whenever `condition_on_previous_text` is False or the temperature fallback exceeds `prompt_reset_on_temperature` [CODE L1377–1388](https://github.com/SYSTRAN/faster-whisper/blob/a52b026ac844c02117865e61968547639b60a6f8/faster_whisper/transcribe.py#L1377-L1388). `hotwords` is passed to `get_prompt` for every window [CODE L1207–1210](https://github.com/SYSTRAN/faster-whisper/blob/a52b026ac844c02117865e61968547639b60a6f8/faster_whisper/transcribe.py#L1207-L1210)

### Inferences
- **Use `hotwords`, not `initial_prompt`, for pass 2 on long lectures.** This follows from the code above and was not runtime-tested here. `initial_prompt` affects only the first window when `condition_on_previous_text=False`. When it is True, the prompt is pushed out by accumulated transcript and dropped on any temperature-fallback reset. `hotwords` is injected into every 30-second window.
- **Order the list by priority.** `hotwords` keeps the *head*, so the best-ranked terms go first. `initial_prompt` keeps the *tail*, so they would go last. Huber & Waibel show order sensitivity for prompt-based models, so a deterministic order is also needed for reproducibility.
- **The cap is per decoding window, not per lecture.** Calling transcribe separately per lecture segment (e.g., `clip_timestamps` or chunked calls), each with its own 40-term list chosen from that segment's pass-1 text, mirrors the per-window filtering of 2301.06735 and CB-Whisper's per-utterance prompts. It multiplies effective capacity. Untested.
- A scoring rule built only from published, training-free components:
  - score(term) = α·prior(term) + (1−α)·relevance(term), the Microsoft eq. 11 form;
  - relevance = −min over windows of normalized jamo edit distance between the term and a length-matched pass-1 segment (eq. 10, applied to jamo);
  - add RECOVER-style bonuses for exact hits across hypotheses and for low-`Word.probability` spans;
  - exclude glossary terms already recognized correctly and confidently in pass 1, and exclude very common words (F2).

  The weights must be tuned on our data; published weights (α, 1.0/1.2/0.6) come from other languages and domains.
- **Prefer a spoken-form prompt over a bare comma list.** CB-Whisper shows naive lists can raise MER through lost disfluencies or hallucination, and lecture speech is spontaneous. The spoken-form wrapper costs tokens out of the 223, which is a direct trade against list length.
- **Avoid near-homophone glossary pairs in one list** unless both are supported by pass-1 evidence (ANN-P, Alon, BR-ASR). Whisper is not trained with hard negatives, so it cannot be assumed to discriminate them.

### Gaps
- No paper measures the optimal list size *for Whisper prompting* specifically (e.g., 10 vs 40 vs 100 terms with controlled distractors). Huber & Waibel test speech LLMs and dedicated biasing methods, and CB-Whisper tests predicted versus oracle lists, not size sweeps.
- The faster-whisper behaviour above comes from reading code. The combined length when both `hotwords` and previous text are present, and the interaction with batched inference (which sets `condition_on_previous_text=False`, [CODE L549](https://github.com/SYSTRAN/faster-whisper/blob/a52b026ac844c02117865e61968547639b60a6f8/faster_whisper/transcribe.py#L549)), were not runtime-tested.
- No evidence was found on Korean tokenization density (tokens per term) in Whisper's BPE. It decides how many terms fit in 223 tokens and needs measuring locally.

---

## Q4. Selecting terms by "how often misrecognized" vs "how often they occur" vs "how rare/hard they are"

### Takeaway
Peer-reviewed systems almost always select by **rarity** (outside the top-5,000 common words) combined with **relevance to the current hypothesis**. Frequency or popularity enters only as an optional prior (Microsoft's preference ranker). Choosing terms by observed *misrecognition* frequency appears only in practitioner guidance. Modelling *how* a term is misrecognized (alternate spellings, confusion n-grams, proxies) has strong peer-reviewed support. No controlled comparison of the three criteria was found.

### Cited Findings
- **Rarity as the definition of a biasing word**: "outside the 5,000 most common words in the audio training set (which account for 90% of all word occurrences)." Distractors are drawn from 209.2K rare words, and lists cover 10.6% of test words — [Le et al., arXiv:2104.02194](https://ar5iv.labs.arxiv.org/html/2104.02194)
- **Common-word removal before matching**: removing the top-5,000 words gave a 7.82%/6.41% relative WER/B-WER reduction on test-other versus no removal (F2 vs F1) — [arXiv:2411.06437](https://arxiv.org/html/2411.06437v1)
- **Frequency or preference prior**: the preference ranker encodes "the call frequency of context phrases in the target domain," mixed with relevance through α_p — [arXiv:2203.00888](https://arxiv.org/abs/2203.00888)
- **Modelling the misrecognition**:
  - Predicted alternate spellings added to the bias list raised rare-word recall by 34.7% relative and OOV recall by 97.2% relative over biasing without them, with no pronunciation dictionary or TTS needed — [Fox & Delworth, Interspeech 2022](https://www.isca-archive.org/interspeech_2022/fox22_interspeech.html); [arXiv:2209.01250](https://arxiv.org/abs/2209.01250)
  - Learned misspelling n-grams give 74–90% top-10 recall — [SpellMapper](https://ar5iv.labs.arxiv.org/html/2306.02317)
  - Phone-confusion proxies gave up to 40% ATWV — [ASRU 2013](https://www.clsp.jhu.edu/wp-content/uploads/2015/11/asru2013_proxy_keyword.pdf)
- **Practitioner guidance [PRAC]**:
  - "add the terms that show up most often in your sessions and are most frequently mistranscribed" — [Great Question docs](https://greatquestion.co/support/repository/transcript-keyterm-prompting) (provider not disclosed)
  - "Start small: Begin without any boosting; add keywords/keyterms only where needed … Focus on proper nouns or domain terms the model often misses" — [Vapi docs](https://docs.vapi.ai/customization/custom-keywords)
  - Avoid generic words that are "rarely misrecognized" — [Deepgram docs](https://developers.deepgram.com/docs/keyterm)
- **Tail entities benefit most**: kNN correction gains are reported mainly on rare-word and OOV entity sets (7.4% and 9.8% relative). This is paraphrased from a summarized read, not a direct quote — [arXiv:2109.05092](https://arxiv.org/abs/2109.05092). Pusateri et al. report head/torso/tail splits — [arXiv:2409.06062](https://arxiv.org/abs/2409.06062)

### Inferences
- Within one recording, the most defensible criterion follows from Q1–Q3: **evidence of presence in this audio** (a hypothesis-matched near-miss, preferably in a low-confidence span), multiplied by **rarity or difficulty for Whisper**. Terms recognized correctly in pass 1 add little; an oracle-list experiment (Q5) can test this.
- Across a professor's lectures, a per-glossary record of which terms Whisper actually got wrong is a cheap prior: the α·W_p term. It approximates the practitioners' "most frequently mistranscribed" rule without mixing glossaries.

### Gaps
- No peer-reviewed study found compares selecting by misrecognition frequency versus occurrence frequency versus rarity for a fixed list budget. This should be treated as an open empirical question for our data.
- No study was found on per-speaker or per-course "error-history" priors for biasing-list selection.

---

## Q5. Evaluation metrics for biasing and list selection

### Takeaway
The standard is **B-WER / U-WER** (Le et al. 2021), with insertions of list words counted toward B-WER. List selection itself is evaluated with **recall of the true entities after filtering** (ERR, recall@k), **list size** (ALS), and **precision of the retrieved list**. System safety is checked with **anti-context / mismatched-list sets** and an **oracle-list topline**. Character-based languages use CER/MER analogues (B-CER), and entity recall is reported alongside.

### Cited Findings
- **B-WER**: WER over words in the biasing list; "Insertion errors are counted toward B-WER if the inserted word is in the biasing list." **U-WER**: WER over the remaining words, with other insertions counted there — [Le et al., arXiv:2104.02194](https://ar5iv.labs.arxiv.org/html/2104.02194). The pair is widely reused: LLM-ASR in [arXiv:2411.06437](https://arxiv.org/html/2411.06437v1), Whisper/LLM comparison in [arXiv:2608.05759](https://arxiv.org/html/2608.05759), BR-ASR in [arXiv:2505.19179](https://arxiv.org/html/2505.19179v1).
- **Filter metrics**: Entity Recall Rate (recall of ground-truth entities after filtering) and Average List Size — [arXiv:2301.06735](https://arxiv.org/abs/2301.06735). Retrieval recall@1/5/10 — [arXiv:2409.06062](https://arxiv.org/html/2409.06062v1). Top-10 candidate recall — [SpellMapper](https://ar5iv.labs.arxiv.org/html/2306.02317). Candidate precision/recall before and after denoising — [DeRAGEC](https://arxiv.org/html/2506.07510v1). Pruning rate (99.99%) — [BR-ASR](https://arxiv.org/html/2505.19179v1).
- **Detection metrics**: KWS F1 — [CB-Whisper](https://arxiv.org/abs/2309.09552), [KG-Whisper](https://arxiv.org/html/2406.02649v1). Spotting precision/recall/F-score — [CTC-WS](https://arxiv.org/html/2406.07096v1). Keyword error rate (KER) — [arXiv:2512.21828](https://arxiv.org/abs/2512.21828). Keyword Recall Rate (KRR) — [Deepgram](https://developers.deepgram.com/docs/keyterm) [PRAC].
- **Entity-level metrics**: entity recall plus MER — [CB-Whisper](https://arxiv.org/abs/2309.09552). Entity-phrase WER, with substitution/deletion breakdown — [RECOVER](https://arxiv.org/html/2603.16411v1). NE hit ratio — [DeRAGEC](https://arxiv.org/html/2506.07510v1).
- **Regression and false-biasing checks**:
  - anti-context set of "distractor entities only" — [Deferred NAM](https://arxiv.org/html/2404.10180v2)
  - general-domain set (Random8k) with threshold s_o trading biasing-set WER against anti-context WER — [Microsoft CSC](https://arxiv.org/abs/2203.00888)
  - mismatched-domain list (UD) — [arXiv:2301.06735](https://arxiv.org/abs/2301.06735)
  - STOP as a no-regression set — [arXiv:2409.06062](https://arxiv.org/abs/2409.06062)
  - share of segments improved versus degraded (40.1% vs 7.1%) — [Courtside](https://arxiv.org/abs/2602.18966)
  - false-positive warning for boosting — [Google](https://docs.cloud.google.com/speech-to-text/docs/boost) [PRAC]
- **Ceiling metrics**: an oracle-list topline system — [arXiv:2301.06735](https://arxiv.org/abs/2301.06735); an oracle prompt with ground-truth entities — [CB-Whisper](https://arxiv.org/abs/2309.09552); "recovery" as the fraction of the theoretical maximum improvement achieved (49–68%) — [SpellMapper](https://ar5iv.labs.arxiv.org/html/2306.02317).
- **Cost**: RTF — [arXiv:2301.06735](https://arxiv.org/abs/2301.06735); decoding seconds — [CTC-WS](https://arxiv.org/html/2406.07096v1); per-query latency — [Deferred NAM](https://arxiv.org/html/2404.10180v2), [BR-ASR](https://arxiv.org/html/2505.19179v1).

### Inferences
Candidate metric set for our two-pass Korean lecture setting, all computable from a reference transcript and the glossary. These are proposals, not established practice for Korean:
1. **Selection recall (ERR analogue)**: among glossary terms spoken in the lecture *and misrecognized in pass 1*, the fraction that appear in the 40-term list.
2. **List precision**: the fraction of listed terms actually spoken.
3. **B-CER and U-CER**: CER over glossary-term spans versus everything else, attributing inserted glossary terms to B-CER (Le et al.'s rule at character level).
4. **False-biasing insertions**: glossary terms in pass 2 that are absent from the reference.
5. **Introduced errors**: terms or spans correct in pass 1 but wrong in pass 2, plus improved/degraded segment counts as in Courtside.
6. **Oracle topline**: pass 2 with the list built from the reference transcript (capped at 223 tokens), to separate selection error from Whisper's prompt-following limits.
7. **Cost**: pass-2 RTF on the RTX 2070.

### Gaps
- No Korean-specific B-CER benchmark or a standard way to segment Korean glossary terms for B-WER was found. Term boundaries under particles and spacing (e.g., "간염이", "간염은") need a local convention.
- No sources were found that report false-biasing rates specifically for Whisper prompt biasing at fixed list sizes.
