# LLM-based error detection and generative error correction (GER) for ASR transcripts, with domain glossaries/entity lists, and its risks

Scope note: peer-review status is marked per source. "Preprint" = arXiv-only, not peer-reviewed (or venue not verified). "Local code inspection" = verified by reading the installed package in this repo, not literature. Numbers are quoted as reported; WER/CER = word/character error rate; "rel." = relative.

---

## 1. Generative error correction from N-best hypotheses (HyPoradise, GenSEC, Whispering LLaMA, LLM rescoring): gains, when it helps or hurts, named entities/rare words

### Takeaway
N-best GER with a *fine-tuned* 7B–13B LLM gives large gains on noisy or out-of-domain English sets (−40% to −77% rel. WER on WSJ, ATIS, CHiME-4) and sometimes beats the N-best oracle. On low-WER inputs it gives little or nothing, and sometimes makes WER worse. Zero-shot correction of only the 1-best output with an API LLM is unreliable. The evidence on named entities and rare words is mostly case studies until the retrieval-augmented papers (§2).

### Cited Findings
**Benchmarks and gains**
- HyPoradise (NeurIPS 2023 Datasets & Benchmarks; peer-reviewed): the first open benchmark for LLM-based ASR error correction, with >334K (N-best list, reference) pairs. It claims results that "surpass the upper bound of traditional re-ranking based methods" and that LLMs can "correct those tokens that are missing in N-best list" — [arXiv:2309.15701](https://arxiv.org/abs/2309.15701)
- HyPoradise per-dataset WER, baseline → H2T-LoRA (LLaMA-13B + LoRA). Oracle n-best (o_nb) and oracle compositional (o_cp) in brackets: WSJ 4.5→2.2 (−51.1%) [o_nb 4.1, o_cp 1.2]; ATIS 8.3→1.9 (−77.1%) [5.2, 1.1]; CHiME-4 11.1→6.6 (−40.5%) [9.1, 2.8]; LibriSpeech-clean 1.8→1.7 (−5.6%) [1.0, 0.6]; LibriSpeech-other 3.7→3.8 (**+2.7%**) [2.7, 1.6]; CommonVoice-accent 14.8→11.0 (−25.7%); TED-LIUM-3 8.5→4.6 (−45.9%); SwitchBoard 15.7→14.1 (−10.2%); CORAAL 21.4→19.2 (−10.3%) — [HyPoradise HTML](https://arxiv.org/html/2309.15701)
- HyPoradise failure conditions: full fine-tuning of T5 (H2T-ft) made LibriSpeech-other worse by +5.4% and did not improve CORAAL (21.4→23.1) or SwitchBoard (15.7→15.9). The authors say "when the WER is low … there is less room to correct", point to utterance length and long-form speech as factors, and report an "over-fitting phenomenon … especially in H2T-ft" — [HyPoradise HTML](https://arxiv.org/html/2309.15701)
- HyPoradise models: T5 (0.75B–3B), LLaMA (7B–65B; 13B used for LoRA), and GPT-3.5-turbo for zero-/few-shot in-context learning (ICL), where GPT-3.5 improved from 0-shot to 10-shot — [HyPoradise HTML](https://arxiv.org/html/2309.15701)
- GenSEC challenge, Task 1 (SLT 2024 challenge paper; NVIDIA lists it as Dec 2024): maps N-best hypotheses to the ground-truth transcript, built on HyPoradise, with 316,881 training pairs. Baseline Whisper-1.5B test WER 11.82% → LLaMA2-7B GER 8.33% → T5-750M correction 9.05% (LLaMA-13B was also a baseline). The paper gives no per-subset or named-entity breakdown in the sections retrieved — [arXiv:2409.09785](https://arxiv.org/html/2409.09785v2); [NVIDIA Research listing](https://research.nvidia.com/publication/2024-12_large-language-model-based-generative-error-correction-challenge-and-baselines)
  - Note: HyPoradise reports ">334K" pairs and GenSEC reports 316,881 *training* pairs. The difference is likely train-vs-total, but this was not verified.
- FlanEC (SLT 2024 GenSEC track): Flan-T5 encoder-decoder that maps N-best lists to one sentence on HyPoradise, studying how data scale affects results. No numbers were retrieved — [arXiv:2501.12979](https://arxiv.org/abs/2501.12979)
- Whispering LLaMA (EMNLP 2023; peer-reviewed): cross-modal GER that fuses Whisper-encoder acoustic features with the N-best list inside an LLM using parameter-efficient adapters, for 37.66% rel. WER improvement over the N-best baseline. Model size is not stated in the abstract — [arXiv:2310.06434](https://arxiv.org/abs/2310.06434)
- Task-activating prompting (Yang et al., ASRU 2023; peer-reviewed): a frozen LLM doing rescoring and GER through instruction/ICL prompts is competitive with domain-tuned LMs on ATIS and WSJ; combined with fine-tuning it reaches "error rates below the N-best oracle level" — [arXiv:2309.15649](https://arxiv.org/abs/2309.15649)

**1-best vs N-best, zero-shot, and constrained output** (Ma, Qian, Gales, Knill; arXiv, "submitted for publication" to IEEE; final venue not verified)
- Quote: "using only the top one ASR hypothesis as input, ChatGPT-based error correction may experience a degradation in performance." — [arXiv:2409.09554](https://arxiv.org/html/2409.09554)
- GPT-3.5 zero-shot on LibriSpeech test_other (Transducer ASR): 1-best input 8.25% WER vs 5-best 6.64% vs 10-best 6.69%. TED-LIUM3: 11.95% (1-best) vs 11.35% (5-best). Artie Bias: 21.19% vs 18.73% — [arXiv:2409.09554](https://arxiv.org/html/2409.09554)
- On Whisper outputs, zero-shot GPT-4 averaged only 2.6% WERR, against 25.2% WERR on Transducer outputs. Strong ASR leaves little for zero-shot correction — [arXiv:2409.09554](https://arxiv.org/html/2409.09554)
- Unconstrained ChatGPT correction raised deletion errors because "some sentences are truncated in the ChatGPT output" — [arXiv:2409.09554](https://arxiv.org/html/2409.09554)
- Constrained decoding helps when the correction model meets output from a different ASR system:
  - Transducer-trained EC on Whisper output: unconstrained 3.86% WER (no improvement over baseline); N-best-constrained 2.62% (reported as 6.1% rel. improvement on test_clean).
  - Whisper-trained EC on Transducer test_other: unconstrained 7.72% (worse than baseline); N-best-constrained 6.65% (3.6% rel. improvement).
  - Supervised models in this paper are T5-base — [arXiv:2409.09554](https://arxiv.org/html/2409.09554)

**Gating which utterances go to the LLM**
- Multi-stage LLM correction (Pu, Nguyen, Stüker; preprint): estimates uncertainty from the N-best list to choose which utterances to send to the LLM, then corrects with "multi-step rule-based LLM reasoning". Reports "10% ~ 20% relative improvement in WER" zero-shot across domains — [arXiv:2310.11532](https://arxiv.org/abs/2310.11532)

**Named entities and rare words in N-best GER**
- HyPoradise shows one case study: the company name "Sinopec", absent from ASR training, recovered from "xinnepec" plus context. Anecdotal, no aggregate entity metric — [HyPoradise HTML](https://arxiv.org/html/2309.15701)
- Yamashita et al. (Interspeech 2025; peer-reviewed): standard LLM GER "often struggles with rare or domain-specific words", and relying on text alone "leads to over-correction". Fix: synthetic rare-word training data plus N-best and phonetic context. Reports lower WER/CER on English and Japanese; no numbers in the abstract — [arXiv:2505.17410](https://arxiv.org/abs/2505.17410v1)

### Inferences
- Our ASR is faster-whisper large-v3 (local code inspection: `/home/kjeong/localgit/stt/pyproject.toml`; `tests/test_metadata.py`), a strong model.
  - Ma et al. (2.6% WERR for GPT-4 on Whisper output) and Naderi et al. (§5: Large-V3 got worse after correction) both suggest that whole-utterance GER on *general* words will give near-zero or negative returns.
  - Value is more likely to come from targeted domain-term substitution (§2) than from free rewriting.
- N-best GER needs N-best lists. faster-whisper exposes word timestamps and probabilities by default, not N-best lists. Getting N-best would mean re-decoding with beam outputs kept, which is extra engineering, not verified here.
- The strongest N-best gains come from models fine-tuned on (hypothesis, reference) pairs for the target ASR and domain. We have no Korean reference transcripts for microbiology lectures, so we cannot directly reproduce the HyPoradise/GenSEC conditions.

### Gaps
- None of the N-best GER papers retrieved report entity-level or term-level precision, or false-correction rates, on a domain lexicon.
- I found no Korean N-best GER benchmark comparable to HyPoradise.
- The Whispering LLaMA model size and per-dataset numbers were not retrieved.

---

## 2. Glossary/entity-aware LLM correction: retrieval-augmented correction, phonetic candidate retrieval, constrained/edit-only outputs (2023–2026)

### Takeaway
The best-documented designs that use an entity list follow one pattern:
1. Detect a suspect span.
2. Retrieve a few catalog entries by phonetic or orthographic similarity.
3. Let a small LLM or encoder choose among them or keep the original.

Reported gains are 18–39% rel. on entity-heavy sets. However, the LLM barely distinguishes among acoustically similar candidates, and few papers measure false substitutions directly. Edit-span outputs and "keep the original on doubt" gates are the main tools in the literature for limiting rewriting.

### Cited Findings
**Inventory of glossary-aware approaches**

| Approach (venue) | What it does / inputs | Model, local? | Reported gains | Failure modes / controls reported |
|---|---|---|---|---|
| Retrieval-Augmented Correction (Apple; preprint, venue not verified) | Generates queries from 1-best (all n-grams / regex templates / NE tagger); retrieves from a **2.6M-entity catalog** with BM25, T5 semantic embeddings, or Acoustic Neighbor Embeddings (orthography or phoneme), with distance threshold Dmax and candidate cap Rmax (11 or 55); LLM rewrites the hypothesis | OpenLLaMA 7B + LoRA (rank 4 for correction), greedy decoding; open-weight, so locally runnable | 32.6% / 35.3% / 39.0% rel. WER reduction on synthetic head/torso/tail music-entity sets; 7.7% rel. on public STOP set "without regressing" — [arXiv:2409.06062](https://arxiv.org/html/2409.06062v1) | Entity recall: top-1 85.6%, top-5 92.1% (phoneme embeddings, head). No analysis of hallucination or over-correction. "Lack of significant improvement when using multiple matches suggests that the adapted LLM lacks any intelligence that would help distinguish between acoustically similar entities" — [same](https://arxiv.org/html/2409.06062v1) |
| RASTAR (preprint, Jan 2026) | BERT "rephrasing LM" finds entity spans → **phonetic retrieval by romanization + normalized edit distance** over 16,168 entities → reasoning LLM with adaptive chain-of-thought | Qwen3-0.6B and 8B (local-capable) | NE-CER −17.96% rel. (AISHELL-1), −34.42% rel. (homophone set, 115 utterances); NE recall 89.33% / 85.80% — [arXiv:2602.12287](https://arxiv.org/html/2602.12287v1) | Mandarin only; no false-correction rate reported — [same](https://arxiv.org/html/2602.12287v1) |
| SpellMapper (NVIDIA; Interspeech 2023) | Non-LLM: n-gram-mapping candidate retrieval from user vocabulary + BERT that sees transcript + 10 candidates | BERT-size, local | Up to 90% recall at top-10 candidates (Spoken Wikipedia); 21.4% WER improvement — [arXiv:2306.02317](https://arxiv.org/abs/2306.02317) | False positives not discussed in the abstract |
| PMF-CEC (IEEE TASLP 2025) | Phoneme-augmented fusion of ASR output + biasing list; "retention probability" keeps the original token when an edit's confidence is below threshold, to reduce "overdetection" | Non-LLM; described as faster and more robust than LLM-based alternatives | Lower biased-word error rate on 5 datasets (numbers not retrieved) — [arXiv:2506.11064](https://arxiv.org/abs/2506.11064) | Built specifically against false positives on homophones |
| Rare-word phonetic GER (Interspeech 2025) | N-best + phonetic context + synthetic rare-word fine-tuning | LLM size not retrieved | Better rare-word correction; lower WER/CER on English and Japanese — [arXiv:2505.17410](https://arxiv.org/abs/2505.17410v1) | Text-only GER "leads to over-correction"; phonetics added to mitigate it |
| RLLM-CF three-stage (preprint) | (1) LLM error pre-detection → (2) chain-of-thought steps: localization, pronunciation assessment, candidate generation, candidate selection → (3) verification; **falls back to original input** if verification fails | GPT-4o and DeepSeek-V2 (API) | AISHELL-1 CER 5.17→4.06 (−21% rel.); AISHELL-2 −11%; LibriSpeech clean −9%, other −11.4% — [arXiv:2505.24347](https://arxiv.org/html/2505.24347v3) | Plain prompting raised CER from 5.06% to **53.10%** in one ablation. 98 correct sentences were corrupted even with the framework (see §5) — [same](https://arxiv.org/html/2505.24347v3) |
| Error-aware TF-IDF RAG (preprint) | Retrieves documents containing historically high-risk misrecognitions using an error-weighted TF-IDF (purely lexical) for LLM correction | LLM not stated | Persian FLEURS: hit rate 53.7%→90.9%; WER 23.06%→18.83% — [arXiv:2606.24915](https://arxiv.org/abs/2606.24915) | Over-correction not discussed |
| Medical few-shot term prompting (FAU; Current Directions in Biomedical Engineering, Sep 2024) | Whisper-medium + GPT-3.5/GPT-4, prompts that include medical terms | API | "GPT-3.5 with few-shot medical terms prompting achieves 64.29% and 37.83% WER-reduction" (**search-snippet only; full text not opened**) — [FAU record](https://open.fau.de/handle/openfau/35043) | Not verified |

**Edit-only, span-limited, and constrained outputs**
- N-best/lattice-constrained decoding makes the corrector choose an output supported by the ASR search space. It turned degradation into gains when the correction model met output from a different ASR system (numbers in §1) — [arXiv:2409.09554](https://arxiv.org/html/2409.09554)
- Compact phrasal rewrites (Zhang, Stahlberg, Kumar; ICASSP 2025):
  - Prior edit-span representations cut LLM output length by ~80% with minimal accuracy loss.
  - The proposed phrasal/target-phrase-only edits "close 50–60% of the WER gap between the edit span model and the full rewrite model" while giving up only 10–20% of the length reduction.
  - [arXiv:2501.13831](https://arxiv.org/abs/2501.13831)
- Context-Enhanced Granular Edit Representation (CEGER): LLMs emit "highly structured, fine-grained … editing commands" rather than full text (preprint; details not retrieved) — [arXiv:2509.14263](https://arxiv.org/html/2509.14263v1)
- Chain of Correction (ICASSP 2026): corrects full-text ASR "segment by segment" in a multi-turn chat, guided by full-text context. The authors "analyze correction thresholds to balance under-correction and over-rephrasing" (numbers not retrieved) — [arXiv:2504.01519](https://arxiv.org/abs/2504.01519)
- Judge–Editor agent (ICASSP 2026; dysarthric speech):
  - Policy: "preserve spans that show high cross-hypothesis agreement; rewrite or fuse only uncertain segments; and avoid hallucinating content, especially named entities and numbers."
  - Uses top-5 hypotheses, 7B–8B local models (Qwen2/Qwen3/Llama), LoRA with int8.
  - Results: 14.51% WER reduction; <0.6% degradation on already error-free utterances (fine-tuned).
  - [arXiv:2601.21347](https://arxiv.org/html/2601.21347v1)

### Inferences
- The professor's term list matches the "entity catalog" setting of Apple RAC and RASTAR (term list, no textbook needed). The catalog is small (thousands of terms vs Apple's 2.6M), so exhaustive phonetic matching of every transcript span against the glossary is computationally trivial. Recall should be bounded by the phonetic matcher, not the index.
- The Apple finding that the LLM cannot pick well among acoustically similar entities suggests the selection signal should come from acoustics and context, not LLM priors. Concretely: ASR word probability, phonetic distance, and co-occurrence with lecture topic.
- For Korean, romanization or jamo-level edit distance (analogous to RASTAR's romanization) is the natural phonetic key. This is untested in the literature for Korean medical terms.
- An output schema of *substitution records* rather than rewritten text matches edit-span work and our verbatim-fidelity constraint: {segment id, char offsets, original span, glossary term id, replacement, evidence, score}. It makes every edit loggable and reversible by construction.

### Gaps
- No paper found that measures **false-substitution rate when the correct term is absent from the catalog** (out-of-list speech) for LLM RAG correction. Apple RAC does not analyze it.
- No Korean study of glossary-constrained LLM correction for technical or medical lectures was found.
- Constrained decoding that restricts LLM *tokens* to glossary entries (grammar/trie-constrained generation) for ASR correction: no ASR-specific paper retrieved. Only N-best/lattice constraints (Ma et al.).

---

## 3. LLMs (and retrievers) for selecting or generating biasing lists, then re-decoding

### Takeaway
The working pattern for large term inventories is first-pass decode → shortlist (by CTC/first-pass text match, speech–text contrastive retrieval, or LLM agents) → inject a compact list into the second pass. LLM-agent selection of prompt terms has one preprint (sports commentary, −17% rel. WER, with 7.1% of segments made worse). Retrieval-based shortlisting is better documented and scales to 200K entries.

### Cited Findings
- CTC-assisted LLM-based contextual ASR (SLT 2024): uses "coarse CTC decoding results to filter potential relevant hotwords and incorporating them into LLM prompt input". Works with 2,000 biasing words. LibriSpeech test-clean WER 1.27% / B-WER 3.67%; test-other WER 2.72% / B-WER 8.02% — [arXiv:2411.06437](https://arxiv.org/abs/2411.06437)
- BR-ASR (Interspeech 2025):
  - Speech–bias contrastive retrieval plus curriculum learning to handle homophone confusion, with biasing lists up to 200,000 entries.
  - B-WER 2.8% / 7.1% (test-clean/other) with 2,000 bias words, 45% rel. over prior methods.
  - Scaling to 200K entries costs only 0.3% / 2.9% absolute WER/B-WER; 99.99% pruning, 20 ms per query.
  - [arXiv:2505.19179](https://arxiv.org/abs/2505.19179)
- Hotword retrieval + reinforcement learning (preprint, Dec 2025): an extended GLCLAP retriever plus fuzzy matching picks top-k hotwords from a large vocabulary. They are injected as text prompts into an LLM-ASR fine-tuned with GRPO. Reports "substantial keyword error rate (KER) reductions"; numbers not retrieved — [arXiv:2512.21828](https://arxiv.org/abs/2512.21828v1)
- Whisper: Courtside Edition (preprint):
  - Pipeline: first-pass Whisper → multi-agent LLM (domain-context identification, NER, jargon detection) → compact prompt → second-pass Whisper decoding.
  - 421 NBA commentary segments: WER 0.217→0.180 (−17.0% rel., p<0.001); improved 40.1% of segments, **degraded 7.1%**.
  - LLM identity and size not stated in the abstract.
  - [arXiv:2602.18966](https://arxiv.org/abs/2602.18966)
- Contextual biasing with Whisper and GPT-2 via tree-constrained pointer generator (TCPGen), without changing Whisper weights (Interspeech 2023). Background only; details not retrieved — [arXiv:2306.01942](https://arxiv.org/pdf/2306.01942)
- Local code inspection, faster-whisper 1.2.1 in this repo:
  - `hotwords` is encoded and **truncated to `max_length // 2 - 1` tokens** (`transcribe.py:1544-1548`), with `max_length = 448` (`transcribe.py:722`). That is at most 223 prompt tokens per window, and it "has no effect if prefix is not None" (docstring, `transcribe.py:345-346`).
  - The `Word` dataclass carries `probability` (`transcribe.py:36`).
  - Path: `/home/kjeong/localgit/stt/.venv/lib/python3.12/site-packages/faster_whisper/transcribe.py`

### Inferences
- A glossary of "thousands of terms" cannot fit the ~223-token hotword budget (Korean subword tokenization makes it smaller still), so per-segment selection is mandatory.
- Literature-backed selectors that need no audio-side training:
  - (a) Match first-pass text against the glossary, string/phonetic, analogous to CTC-assisted filtering.
  - (b) LLM-agent selection given first-pass text, the Courtside pattern.
- (a) is deterministic and auditable. (b) adds the non-determinism discussed in §6.
- Courtside's 7.1% degraded segments is a reminder that prompt biasing can *create* errors. Re-decoding should therefore be evaluated per segment against the first pass and kept as an alternative, not overwritten.

### Gaps
- No paper found where an LLM picks biasing terms from a *professor-supplied catalog* for lecture audio, or in Korean.
- No quantitative study found on Whisper `initial_prompt`/hotword prompt length vs. hallucination for long glossary prompts. Practitioner claims exist, but nothing peer-reviewed was retrieved.
- The Courtside LLM models and costs are not reported in the abstract.

---

## 4. Korean/multilingual results; small local models (≤8B, quantized) vs API models; feasibility on an 8 GB GPU shared with ASR

### Takeaway
Korean evidence is thin but consistent:
- General LLMs over-correct error-sparse Korean transcripts badly.
- Zero/few-shot GPT-4o-mini did not help Korean domain terms.
- Small, task-specific Korean encoders with detector gating beat fine-tuned 7–8B LLMs.

Across low-resource languages, small fine-tuned models beat larger LLMs. On an 8 GB GPU, an 8B INT4 model (~6–7 GB at moderate context) cannot co-reside with Whisper-large, so the steps must run sequentially.

### Cited Findings
**Korean**
- DasanCallDial / DCSC (Engineering Applications of Artificial Intelligence vol. 183, 2026; peer-reviewed journal per arXiv metadata):
  - Data: 1,974 Korean call-center dialogues, 115,460 utterances — [arXiv:2609.09889](https://arxiv.org/abs/2609.09889)
  - Baselines: fine-tuned Llama-3.1-8B-Instruct and Qwen2.5-7B-Instruct. Llama-3.1 with direct utterance-to-utterance correction produced N-WER (WER on error-free "normal" utterances) of **83.29**, against 0 for no correction.
  - Llama-3.1 error detection: precision **11.63**, recall 46.88, F1 18.64.
  - DCSC uses koelectra-base-v3 as token-level detector and pko-t5-base as span corrector. Balanced WER 14.67→13.30; error-specific WER 29.35→26.27; normal-utterance WER 0.35; detection F1 72.91 — [arXiv HTML](https://arxiv.org/html/2609.09889)
- Korean meteorological ASR (KAIST; preprint, v3 Sep 2025):
  - 5,500 weather queries from 11 speakers; 24.86% of unique words absent from general corpora.
  - Whisper-large-v2 zero-shot CER 18.35%.
  - GPT-4o-mini GER, zero-shot and 20-shot with a domain prompt, "did not improve performance"; the authors conclude "text-only refinement is insufficient without acoustic adaptation".
  - Example failure: "CALM" misrecognized as "컴".
  - [arXiv:2410.18444](https://arxiv.org/html/2410.18444)
- Korean spoken QA cascades (preprint, 2026): single-character ASR errors are "a distinct semantic-failure channel" because "Sino-Korean morphemes are realized as single syllables, making character-level ASR errors semantic rather than merely orthographic" (quoted from search snippet of the PDF) — [arXiv:2605.17443](https://arxiv.org/pdf/2605.17443); [alphaXiv abstract](https://www.alphaxiv.org/abs/2605.17443)

**Other languages**
- Hindi (EACL 2026 short; peer-reviewed): "smaller fine-tuned models consistently surpass larger LLMs" — mT5/ByT5 vs fine-tuned Nanda-10B, GPT-4o-mini, and LLaMA variants. Under zero-shot ICL, scaling is non-monotonic: mid-sized LLMs degrade performance, larger ones recover only marginally, and all still fall short of fine-tuned models. The two retrieved renderings of the abstract describe the curve's shape differently ("U-shaped inverse scaling" vs "inverted n-shaped"). Out-of-domain degradation is also noted. Same trend in Marathi and Telugu — [ACL Anthology 2026.eacl-short.45](https://aclanthology.org/2026.eacl-short.45/)
- Japanese (IBM, EMNLP 2024 Industry; peer-reviewed): LLM EC fine-tuned on noisy (hypothesis, reference) pairs over-corrects out of domain. "Conservative data filtering" trains the model to make no correction unless the target (1) improves linguistic acceptability and (2) is inferable from context such as source phonemes. It "significantly reduce[s] overcorrection" across 21 internal benchmarks — [arXiv:2407.13300](https://arxiv.org/abs/2407.13300)
- Mandarin: RLLM-CF uses API models (GPT-4o, DeepSeek-V2) — [arXiv:2505.24347](https://arxiv.org/html/2505.24347v3). RASTAR uses local Qwen3-8B / 0.6B — [arXiv:2602.12287](https://arxiv.org/html/2602.12287v1)
- Persian: lexical error-aware RAG — [arXiv:2606.24915](https://arxiv.org/abs/2606.24915)

**Small local vs API**
- Gains reported with ≤8B open models:
  - Apple RAC: OpenLLaMA-7B LoRA — [arXiv:2409.06062](https://arxiv.org/html/2409.06062v1)
  - GenSEC best baseline: LLaMA2-7B — [arXiv:2409.09785](https://arxiv.org/html/2409.09785v2)
  - Judge–Editor: 7–8B int8 LoRA — [arXiv:2601.21347](https://arxiv.org/html/2601.21347v1)
  - RASTAR: Qwen3-8B — [arXiv:2602.12287](https://arxiv.org/html/2602.12287v1)
- In most of these, the gain needed task fine-tuning.
- Naderi et al. (Interspeech 2024): GPT-4 beat GPT-3.5 only on Whisper-Tiny output, with "no additional improvements" on Medium/Large — [Idiap PDF](https://publications.idiap.ch/attachments/papers/2024/Naderi_INTERSPEECH_2024.pdf)
- A medical study across Nigeria, UK, and US (preprint) used API models (Gemini 1.5 Pro, Claude 3.5 Sonnet, GPT-4o) at temperature 0–0.1 — [arXiv:2501.15310](https://arxiv.org/html/2501.15310v1)

**GPU memory (vendor docs / project README; not peer-reviewed)**
- Qwen3-8B AWQ-INT4 (transformers): 6,177 MB at 1 token, 7,113 MB at 6,144 tokens, 11,001 MB at 30,720 tokens. Qwen3-4B AWQ-INT4: 2,915 / 3,881 / 7,742 MB. Qwen3-1.7B GPTQ-Int8: 2,229 / 2,901 / 5,590 MB — [Qwen speed benchmark docs](https://qwen.readthedocs.io/en/latest/getting_started/speed_benchmark.html)
- faster-whisper large-v2 on an RTX 3070 Ti 8 GB (beam 5): fp16 4,525 MB; int8 2,926 MB; batch 8 fp16 6,090 MB — [faster-whisper README](https://github.com/SYSTRAN/faster-whisper)
- Whisper Large V3 has 1,550M parameters, the same as Large V2 — [Naderi et al.](https://publications.idiap.ch/attachments/papers/2024/Naderi_INTERSPEECH_2024.pdf)

### Inferences
- An 8B INT4 corrector (~6.2–7.1 GB) plus Whisper-large int8 (~2.9 GB) exceeds 8 GB, so ASR and LLM must run as separate sequential stages, unloading ASR first. A 4B INT4 model (~2.9–3.9 GB at ≤6K tokens) could co-reside with int8 Whisper in principle.
  - Large-v3 memory is inferred from large-v2's identical parameter count. These are extrapolations, not measurements on our hardware.
- The Korean evidence (DCSC, KAIST meteorology) argues against an unconstrained local 7–8B LLM rewriting Korean lecture text. Lecture transcripts are mostly correct, which is exactly the error-sparse regime where DCSC's fine-tuned Llama scored N-WER 83.29.
- A detector-gated, glossary-limited substitution design is the safer fit, with any LLM used only to choose among glossary candidates.

### Gaps
- No Korean result found for (a) glossary-aware LLM correction, (b) local quantized LLM correction of lectures, or (c) medical/microbiology terminology.
- I found no measured accuracy cost of INT4 quantization specifically for ASR correction tasks.

---

## 5. Making LLM correction auditable: glossary-restricted substitutions, diff logging, confidence thresholds, human review — and evidence on over-correction rates

### Takeaway
Every quantitative study that checked found LLM correction worsens a non-trivial share of utterances:
- ~5–7% of utterances or segments in English studies.
- Hundreds of corrupted words per thousand in unguarded Korean correction.
- Net degradation when the ASR is already strong.

Controls with evidence behind them:
- Gate by ASR confidence or a trained detector.
- Constrain outputs to the N-best list, edit spans, or glossary candidates.
- Verify and fall back to the original.
- Train conservatively.

I found no study evaluating human review of low-confidence LLM edits for ASR.

### Cited Findings
**Over-correction rates (what was measured)**
- Naderi et al. (Interspeech 2024; Whisper Tiny + gpt-3.5-turbo, default API params, LibriSpeech):
  - LLM improved 26.38% of utterances and worsened **4.85%** (dev-clean); improved 29.96% and worsened **6.11%** (dev-other).
  - For Whisper Large-V3, correction raised WER: dev-clean 3.11→3.34 (+7.4% rel.); test-clean 2.78→2.86 (GPT-3.5) and →3.21 (GPT-4) even after confidence filtering.
  - Corrections can lower WER while raising CER (example: WER 57.14%→28.57%, CER 25.00%→27.50%).
  - [Idiap PDF](https://publications.idiap.ch/attachments/papers/2024/Naderi_INTERSPEECH_2024.pdf)
- Medical, three countries (preprint):
  - LLM correction helped weaker ASR (e.g., Azure on PriMock57 0.184→0.122 mean WER) but **hurt** strong ASR (Gemini 1.5 Pro transcripts 0.102→0.129 and 0.062→0.098).
  - Post-correction insertions: medications +12; substance-abuse terms +10 insertions and +6 deletions; biological measurements +6.
  - LLMs fixed ~two-thirds of low-character-difference errors but "struggled with semantically plausible alternatives".
  - [arXiv:2501.15310](https://arxiv.org/html/2501.15310v1)
- RLLM-CF (preprint): of 2,043 sentences flagged by LLM pre-detection, 128 discarded, 347 rejected by verification, and 1,568 modified, yet "98 originally correct sentences were mistakenly altered to erroneous forms". Simple prompting raised CER from 5.06% to 53.10% in one ablation — [arXiv:2505.24347](https://arxiv.org/html/2505.24347v3)
- DCSC (Korean): unguarded fine-tuned Llama-3.1-8B gave N-WER 83.29 on error-free utterances. The detector-gated system gave 0.35 — [arXiv:2609.09889](https://arxiv.org/html/2609.09889)
- Courtside (preprint): LLM-selected prompt terms degraded 7.1% of segments — [arXiv:2602.18966](https://arxiv.org/abs/2602.18966)
- HyPoradise: GER made LibriSpeech-other worse (+2.7% LoRA, +5.4% full fine-tuning) — [HyPoradise HTML](https://arxiv.org/html/2309.15701)
- Ma et al.: unconstrained ChatGPT truncated sentences, raising deletions — [arXiv:2409.09554](https://arxiv.org/html/2409.09554)
- Judge–Editor (fine-tuned 7–8B, span-preserving policy): <0.6% degradation on error-free utterances — [arXiv:2601.21347](https://arxiv.org/html/2601.21347v1)
- Example of a hallucinated "correction" from the RLLM-CF search abstract: GPT-4 turned "DUOLINGO" into "GULAMNBA", WER 0.5→3.0 (from search snippet of the paper; not separately verified) — [arXiv:2505.24347](https://arxiv.org/pdf/2505.24347)

**Controls and their measured effect**
- Confidence gating (Naderi et al.):
  - Send to the LLM only sentences whose Whisper sentence confidence is <0.95, or whose lowest word confidence is <0.7. With Medium, 64–80% of utterances were still passed.
  - Telling the LLM to correct *only* listed low-confidence words was worse: best WER 7.55 at threshold 0.5, vs ~6.65 for sentence-level prompting with the stronger prompt.
  - A prompt asking for "phonetically similar" corrections gave the best dev WER (6.65 vs 6.76 base).
  - [Idiap PDF](https://publications.idiap.ch/attachments/papers/2024/Naderi_INTERSPEECH_2024.pdf)
- Uncertainty-gated multi-stage correction from N-best agreement: 10–20% rel. WER — [arXiv:2310.11532](https://arxiv.org/abs/2310.11532)
- Verification with fallback: the output is accepted only if format and reasoning-step checks pass; "otherwise, the original input is retained" — [arXiv:2505.24347](https://arxiv.org/html/2505.24347v3)
- Constrained decoding to N-best/lattice (§1) — [arXiv:2409.09554](https://arxiv.org/html/2409.09554)
- Edit-span and phrasal outputs instead of full rewrites (§2) — [arXiv:2501.13831](https://arxiv.org/abs/2501.13831)
- Retention-probability gate on edits (PMF-CEC) — [arXiv:2506.11064](https://arxiv.org/abs/2506.11064)
- Detector gating (DCSC) — [arXiv:2609.09889](https://arxiv.org/html/2609.09889)
- Conservative training targets (IBM) — [arXiv:2407.13300](https://arxiv.org/abs/2407.13300)
- Segment-wise correction with thresholds against "over-rephrasing" — [arXiv:2504.01519](https://arxiv.org/abs/2504.01519)
- Span-preservation policy plus deterministic greedy decoding — [arXiv:2601.21347](https://arxiv.org/html/2601.21347v1)

### Inferences
**Design synthesis for our quote-evidence constraint.** This is interpretation, not a tested result.
1. Keep the raw faster-whisper transcript immutable and treat corrections as an overlay layer of substitution records.
2. Allow only substitutions whose replacement is a glossary entry (closed vocabulary) and whose source span passes a phonetic-similarity threshold.
3. Gate by `Word.probability` from faster-whisper, analogous to Naderi's lowest-word gate.
4. Log every edit with its evidence, and make reversal a no-op (drop the overlay).
5. Route edits that touch quoted passages to human review.

Each step maps to a control with published evidence (gating, constrained outputs, fallback, edit spans). The *combination* is untested.

**Why rates mislead.** The over-correction figures are utterance-level rates on English read speech. For quote evidence, the relevant metric is the false-substitution rate per applied edit (precision of applied edits), which most papers do not report. Of the papers retrieved, only DCSC (detection P/R) and RLLM-CF (98 corrupted correct sentences) come close.

### Gaps
- No study found that evaluates **human-in-the-loop review** of low-confidence LLM ASR edits (reviewer time, acceptance rates, residual errors).
- No study reports **precision of applied edits** on a domain glossary, or semantic-change rates (meaning-altering vs orthographic edits), apart from the medical paper's qualitative categories.
- Logging or reversibility designs are not evaluated as such in any paper found. They are engineering practice, not a research result.

---

## 6. Reproducibility and precision of LLM-based misrecognition detection (relevant to the pilot: Claude subagents found ~50–90 terms per slot, with non-deterministic output)

### Takeaway
The literature predicts non-determinism even at temperature 0 on hosted APIs, caused by serving optimizations rather than the models themselves. Local single-request greedy decoding of an 8B model was shown to be deterministic. Published precision for LLM detection of ASR errors ranges from poor (generic fine-tuned 8B on Korean: P=11.6%) to good with gating or task-specific detectors (F1 72.9%). No study matches our setup: LLM plus textbook context, term-level detection, multiple agents.

### Cited Findings
- Atil et al. (preprint, v5 Apr 2025):
  - Setup: GPT-3.5-Turbo, GPT-4o, Llama-3-70B-Instruct, Llama-3-8B-Instruct, and Mixtral-8x7B over 8 tasks and 10 runs, at temperature 0, top-p 1, fixed seed.
  - Findings: "accuracy variations up to 15%"; best-to-worst gap "up to 70%"; "none of the LLMs consistently delivers repeatable accuracy across all tasks, much less identical output strings."
  - Exact-output agreement (TARr@10) ranged 0–99.6% for GPT-4o and fell below 10% for Mixtral on some tasks.
  - Cause attributed to "continuous batching, chunk prefilling, or prefix caching". Llama-3-8B run locally without these optimizations "produced deterministic results".
  - [arXiv:2408.04667](https://arxiv.org/abs/2408.04667); [HTML v5](https://arxiv.org/html/2408.04667v5)
- Judge–Editor (ICASSP 2026): "deterministic greedy decoding is adopted for post-correction to reduce hallucinations and ensure reproducibility" — [arXiv:2601.21347](https://arxiv.org/html/2601.21347v1)
- Naderi et al. called ChatGPT "and do not modify any default parameters", so reported WERs include sampling variance. Single-run results — [Idiap PDF](https://publications.idiap.ch/attachments/papers/2024/Naderi_INTERSPEECH_2024.pdf)
- Detection precision data points:
  - Fine-tuned Llama-3.1-8B on Korean call-center transcripts: P 11.63 / R 46.88 / F1 18.64. Encoder detector (koelectra-base) in DCSC: F1 72.91 — [arXiv:2609.09889](https://arxiv.org/html/2609.09889)
  - RLLM-CF's LLM pre-detection flagged 2,043 sentences; after correction and verification, 98 originally-correct sentences were still corrupted — [arXiv:2505.24347](https://arxiv.org/html/2505.24347v3)
- LLM judgment of ASR errors vs humans:
  - LASER (EMNLP 2025): Gemini 2.5 Pro scores reach "a very high correlation score of 94% with human annotations" for Hindi; a fine-tuned Llama 3 predicts error penalties with ~89% accuracy. Run-to-run consistency not reported — [arXiv:2510.07437](https://arxiv.org/abs/2510.07437)
- Human baseline: in an LREC 2014 study, annotators did not converge on ASR error "gravity" (severity) — [HAL hal-01134802](https://hal.archives-ouvertes.fr/hal-01134802) (finding from search snippet; full text not opened).

### Inferences
- The pilot's variation across subagent runs is the expected behavior of hosted LLMs (Atil et al.). It should not be read as a bug in the prompt.
- Two literature-consistent responses:
  - (a) Freeze detection on a local model with greedy decoding and no batching, for bit-reproducibility.
  - (b) Treat agreement across k independent runs as a confidence signal. This is analogous to Judge–Editor's "cross-hypothesis agreement" (itself applied to ASR hypotheses, not LLM runs), and is an inference, not a tested method for this task.
- Taking the union of runs raises recall and lowers precision. Taking the intersection or a majority does the reverse.
- The ~50–90 candidate terms per slot should be scored against a human-verified subset to estimate precision, because no published figure transfers to Korean microbiology lectures.
- Running the pilot through Claude (API) is a different setting from Atil et al. (who tested GPT/Llama/Mixtral). Similar serving-level non-determinism is plausible but unverified for Claude.
- The textbook context in the pilot has no counterpart in the retrieved literature. All glossary papers use term or entity lists only. That matches our production constraint (term list, no textbook) but means the pilot overstates what a list-only system will achieve.

### Gaps
- No published study measures precision or recall of LLM-based *term-level* misrecognition detection with document (textbook) context, or the reproducibility of multi-agent detection ensembles.
- I could not retrieve current Anthropic documentation on determinism at temperature 0 (the messages API page fetched did not show the parameter text). Claude-specific non-determinism is therefore unverified here.
- No study found on inter-run agreement for Korean ASR error detection specifically.
