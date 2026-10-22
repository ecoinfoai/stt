# Deterministic (non-LLM) lexicon-driven detection and correction of misrecognized domain terms in ASR transcripts, with a focus on phonetic/G2P fuzzy matching and Korean

Scope: static, reproducible methods that use a professor-supplied term list (no definitions) to detect and correct misrecognized terms in Whisper (faster-whisper) Korean lecture transcripts. Neural non-LLM correctors are covered for comparison (Q5). Everything below runs locally unless noted.

Local verification in this session: g2pkiwi 0.1.0 + kiwipiepy 0.20.4, hangul-jamo 1.0.1 and rapidfuzz 3.14.6, run with `uv run --no-project --with g2pkiwi --with "kiwipiepy==0.20.4" --with hangul-jamo --with rapidfuzz python g2ptest2.py`, plus kiwipiepy 0.24.0 for the typo-correction tests. The outputs are quoted in the findings as "local test". Each is labelled as an observation from this session, not a published result.

---

## Q1. Phonetic fuzzy matching: G2P both sides, then match with phonetic edit distance, phoneme n-grams or weighted confusion costs

### Takeaway
The standard deterministic baseline has a name in the literature: "phonetic edit-distance NEC" (PED-NEC). It converts the transcript span and the lexicon entry to phones, then substitutes the entry with the lowest normalized phonetic edit distance. It catches many entity errors cheaply. Its precision falls apart, though, as the lexicon grows and contains near-homophones: on a high-confusion test set it made CER *worse* than doing nothing. Every published system that works pairs phonetic retrieval with a separate gate (a rejection step, a confidence threshold, a coverage check, or an "is this span likely an entity" detector). Phonetic matching should therefore produce *candidates*, and other evidence should decide whether to replace.

### Cited Findings
- **PED-NEC, the definition.** "PED-NEC refines an ASR hypothesis by first detecting all the corrupted entity… then utilizes a phonetic-level matching mechanism based on the edit-distance algorithm to substitute the corrupted entity by matching the most phonetically similar entity picked from the NE list." Similarity is a normalized edit distance over Pinyin. — [Wang et al., DANCER, LREC-COLING 2024 (arXiv:2403.17645)](https://arxiv.org/abs/2403.17645)
- **PED-NEC numbers (Mandarin, AISHELL-1, Conformer ASR).** Overall CER went 4.62 → 4.34 and NE-CER 11.12 → 8.14 (NE recall 78.36 → 84.63%). On the "Homophone" test set, built to have high phonetic confusion, PED-NEC **made things worse**: CER 8.41 → 10.08 and NE-CER 15.58 → 20.88, with NE recall dropping from 70.25 to 56.72%. — [DANCER Table 1](https://arxiv.org/abs/2403.17645)
- **Over-correction of correct text.** Without the entity-rejection step, CER on the *non-entity* part of AISHELL rose from 4.00 (no correction) to 4.65 (PED-NEC without rejection); with rejection it was 4.01. — [DANCER Table 1](https://arxiv.org/abs/2403.17645)
- **Scaling problem.** "When the NE list increases, the phonetic confusion among entities in the NE list grows significantly, which leads to a significant performance drop of PED-NEC". NE recall falls faster for PED-NEC than for DANCER as the list grows. — [DANCER §2.1, Fig. 1](https://arxiv.org/abs/2403.17645)
- **DANCER (neural rerank over phonetic candidates).** It re-ranks the phonetic top-k with an MLM that uses entity *descriptions*. Relative to PED-NEC it lowers NE-CER by about 7% on AISHELL-1 and 46% on Homophone. Code: https://github.com/Amiannn/Dancer (license not stated in the paper). It needs descriptions, which our glossary does not have. — [DANCER abstract](https://arxiv.org/abs/2403.17645)
- **Classic phonetic post-processing of cloud ASR.** Twiefel et al. post-process domain-independent cloud ASR output with "phonetic distance" against domain vocabularies, grammars and n-grams. They report that it "significantly outperforms Google ASR as well as a plain open-source ASR solution" on several HRI corpora. The abstract page carries no numbers. — [Twiefel, Baumann, Heinrich, Wermter, AAAI 2014, doi:10.1609/aaai.v28i1.8929](https://doi.org/10.1609/aaai.v28i1.8929)
- **Edit-distance retrieval: weaknesses named by NVIDIA.** Candidate retrieval by "minimum edit distance between fragments of ASR hypothesis and user phrases… is slow, non-trainable, and may have low recall as it relies only on common letters" (example given: "lookez" vs "lucas"). — [Antonova, Bakhturina, Ginsburg, SpellMapper, Interspeech 2023 (arXiv:2306.02317)](https://arxiv.org/abs/2306.02317)
- **N-gram-mapping retrieval (deterministic at inference).** SpellMapper builds an index offline in two steps:
  1. Collect "misspelled n-gram mappings" (source n-gram → common ASR corruption, with frequencies). Wikipedia titles are synthesized with TTS, transcribed by ASR, and aligned at the character level with GIZA++. This gives 1.9M mappings of at most 5 characters, conditional probability > 0.018, from 4.5M titles at about 80% WER.
  2. For each user vocabulary, index every n-gram of each phrase *and its misspelled variants*.

  At inference it counts n-gram hits per phrase, keeps the phrases with the most hits, and then filters by how well the matched n-grams *cover* the phrase. Top-10 recall was 74–90%. — [SpellMapper §3.1–3.2, Fig. 2–3](https://arxiv.org/abs/2306.02317)
- **Mapping table example.** Corruptions of "luc" with corpus frequencies: luc 7432, luk+e 565, loc 561, luk 382, lo+ok 298, lus 224, lu<DEL> 195. In effect this is an empirically estimated, weighted confusion table. — [SpellMapper Table 1](https://arxiv.org/abs/2306.02317)
- **Microsoft CSC filtering for large lists.** CSC uses a deterministic pre-filter: relevance weight = −min over hypothesis segments of edit_distance(context phrase, segment)/len(phrase), where the segments are cut from the hypothesis at the same length as the phrase. This is combined with a preference ranker to keep the top K_f = 100 of K_r raw phrases. — [Wang et al., "Towards Contextual Spelling Correction…", IEEE/ACM TASLP 2022 (arXiv:2203.00888)](https://arxiv.org/abs/2203.00888)
- **Pronunciations come from a lexicon or a G2P model.** This is the standard choice in phonetic NE-correction pipelines. NPD (normalized phonetic distance) is defined as phonetic edit distance divided by the number of phonemes in the query. — [search summary of NE-correction literature; e.g., DeRAGEC, ACL Findings 2025](https://preview.aclanthology.org/setup/2025.findings-acl.786) (summary-level only; full text not retrieved)
- **Off-the-shelf phonetic matchers are English-only.** Microsoft PhoneticMatching does phoneme-level fuzzy matching (EnPhoneticDistance, EnHybridDistance; EnPronouncer → IPA). It is English only, MIT licensed, has C++/Node/C# APIs, and was **archived Sep 3, 2026**. — [github.com/microsoft/PhoneticMatching](https://github.com/microsoft/PhoneticMatching)
- **Feature-based phone distance tooling.** Epitran supports `kor-Hang`, exposes PanPhon articulatory feature vectors (values in {−1, 0, 1}) per segment, and is MIT licensed. Its README gives no Korean-specific rule notes. — [github.com/dmort27/epitran](https://github.com/dmort27/epitran). PanPhon 0.22.2 (MIT, 2025-06-12): [PyPI](https://pypi.org/pypi/panphon/json); Epitran 1.35.3 (2026-10-08, MIT-Modern-Variant): [PyPI](https://pypi.org/pypi/epitran/json)
- **Generic string/phonetic distance libraries (Python), with licenses:** rapidfuzz 3.14.6 (MIT, 2026-08-30); symspellpy 6.10.0 (MIT, 2026-07-11); abydos 0.5.0 (GPLv3+, 2020-01-11, last release); jellyfish 1.2.1 (MIT; Soundex/Metaphone are English-oriented). — PyPI JSON: [rapidfuzz](https://pypi.org/pypi/rapidfuzz/json), [symspellpy](https://pypi.org/pypi/symspellpy/json), [abydos](https://pypi.org/pypi/abydos/json), [jellyfish](https://pypi.org/pypi/jellyfish/json)
- **Medical-domain precedent (LLM-backed, aggregator source).** MedSpeak retrieves candidates with Double Metaphone plus Levenshtein over UMLS-linked terms and then uses an LLM. Reported WER 77.2% → 29.9%. Source is an aggregator page; the primary paper was not retrieved, and the final stage is an LLM, so it is out of scope except for its retrieval stage. — [emergentmind topic page](https://www.emergentmind.com/topics/medspeak)

### Method inventory table (summary across Q1–Q5)

| Method | What it does | Inputs | Deterministic? | Reported gains / P-R | FP risk | Implementation / license |
|---|---|---|---|---|---|---|
| PED-NEC (phonetic edit distance + NE list) | Replaces a detected span with the nearest entity by normalized phonetic edit distance | Hypothesis, NE list, G2P/romanizer | Matching step yes (span detection in the DANCER setup is a trained detector) | AISHELL NE-CER 11.12→8.14; Homophone set CER 8.41→**10.08** (worse) | High with large or homophone-dense lists; non-entity CER 4.00→4.65 without rejection | Baseline in [DANCER](https://arxiv.org/abs/2403.17645); code [Amiannn/Dancer](https://github.com/Amiannn/Dancer) (license unstated) |
| N-gram-mapping retrieval (SpellMapper stage 1) | Inverted index of term n-grams plus learned corruptions; hit count, then coverage filter | Hypothesis fragments (10–15 words), term list, mapping table | Yes at inference (the table is built offline from TTS+ASR) | Top-10 candidate recall 74–90% | Retrieval only; "repetitive or similar n-grams can produce wrong candidates" | In NeMo ([paper](https://arxiv.org/abs/2306.02317); [NeMo docs](https://docs.nvidia.com/deeplearning/nemo/user-guide/docs/en/main/nlp/spellchecking_asr_customization.html)); English only |
| SpellMapper (full) | Char-level BERT tags hypothesis characters with one of 10 candidates | Same plus trained model (10M synthetic examples) | Model inference only (needs training) | 6.4–21.4% relative WER reduction; recall 53–67%, precision 77.6–88.7% | "Model generates many false positives" (precision 77–87%) | NeMo; English |
| Microsoft CSC/FCSC | Seq2seq (AR/NAR) correction with a shared context encoder | Hypothesis plus up to K_f=100 filtered phrases | Inference only; needs large synthetic training | Name set WER 24.1→11.8 (51%); medical 14.0→8.6 (38.6%); anti-context set 8.8→8.8 | Controlled by output-probability threshold and NER pre-detector | No public code; English ([TASLP](https://arxiv.org/abs/2203.00888)) |
| Jamo-level / G2P-normalized edit distance (Korean) | Levenshtein over jamo, optionally after G2P with vowel grouping | Span, term list | Yes | No published WER for this exact setup; local test: true errors at distance 0–1 | Real-word neighbours at distance 1 (간염/감염, 독소/독서) | g2pk/g2pkiwi (Apache-2.0), hangul-jamo (MIT), rapidfuzz (MIT) |
| Rule-based Korean ASR correction from corpus-extracted rules | Longest-match rules learned from training pairs | Hypothesis, rule table | Yes | Eojeol precision +5.27%, recall +5.60% | Rules learned from errors seen in one corpus | [Kim & Park 2012, doi:10.9708/jksci/2012.17.10.025](https://koreascience.or.kr/article/JAKO201233355899719.pub) |
| Kiwi typo correction | Cost-based typo-aware tokenization | Text (optionally user words) | Yes | No metrics published; local test: corrected 0/8 domain errors | Designed for chat typos, not domain terms | kiwipiepy 0.24.0, Apache-2.0 |
| Whisper word probability gate | Flag words with low probability | faster-whisper word probabilities | Yes, given fixed decoding | Raw-softmax AUC-ROC 0.69–0.81 (English/EU sets, min-aggregation) | Overconfidence: confidently wrong words pass the gate | faster-whisper 1.2.1, MIT |
| Trigram/LM real-word check | Compare LM score of original vs candidate in context | Candidate spans, domain LM | Yes | Good discrimination once a span is selected | Scanning every word "introduces many more" errors | KenLM etc. ([Wilcox-O'Hearn 2014](https://arxiv.org/abs/1408.3153)) |

### Inferences
- For a glossary of thousands of terms, PED-NEC-style "replace with nearest" is unsafe on its own. The DANCER Homophone result is direct evidence that dense phonetic neighbourhoods produce *net-negative* corrections. A deterministic system should treat phonetic distance as a *retrieval* feature and require extra conditions before it auto-replaces. Those conditions are discussed in Q3.
- SpellMapper's candidate retrieval stage (n-gram index plus coverage filter) could be reused without its BERT model. It is cheap, deterministic, and easy to log. The mapping table is the hard part. For Korean, it could be estimated locally from (a) our own aligned corrections over time, or (b) TTS of glossary terms passed through our Whisper setup. Option (b) needs a local Korean TTS, which was not researched here.
- The "coverage" filter (what fraction of the term's characters or jamo are matched) is a cheap extra FP guard, and it is easy to record in an audit log.

### Gaps
- No published Korean evaluation of PED-NEC-style correction with a large technical glossary was found.
- SpellMapper's NeMo implementation license was not checked in this session (NeMo is widely reported as Apache-2.0, but this is unverified here). The DANCER repo license is unknown.
- rapidfuzz's support for per-symbol substitution weights was not verified. Its documented `weights` parameter is believed to be per-operation only, so a per-jamo confusion matrix would likely need a custom DP (unverified).
- No primary source was retrieved for SymSpell's symmetric-delete algorithm; only the symspellpy license and version were verified.

---

## Q2. Korean specifics: G2P tools and the rules they implement, jamo-level edit distance, Korean ASR post-processing research, and analysis of our example errors

### Takeaway
g2pK (Kyubyong) is the reference rule-based Korean G2P. It covers 표준 발음법 제5항–제30항, including 연음, 비음화, 유음화, 경음화, ㅎ-related rules and ㄴ첨가. Its `descriptive` and `group_vowels` options collapse ㅖ→ㅔ, ㅐ→ㅔ and ㅞ→ㅚ, which turns several of our errors into exact phonetic matches. The original is unmaintained (last release 2020) and depends on MeCab. Its forks (g2pkk, g2pkiwi) are also stale, and g2pkiwi only works with a pinned older kiwipiepy.

In the local test on our examples:
- G2P normalization reduces 패혈증/폐혈증 and 궤양/괴양 to distance 0.
- 간염/간념, 원핵/원액, 보체/보처 and 엔테로/엔트로 stay at jamo distance 1.
- The legitimate word 감염 is *also* at distance 1 from 간염.

Phonetics alone therefore cannot separate errors from real words. Korean academic work on lexicon- or phonetics-based ASR post-correction exists but is mostly older and small-scale, and none of it uses Whisper.

### Cited Findings

**G2P tools**
- **g2pK (Kyubyong Park).** README: "g2p module for Korean… pronunciation rules are notoriously complicated". English words written in the Latin alphabet are converted to Hangul via the CMU pronouncing dictionary, and numbers are spelled out in context. It has prescriptive (default) and descriptive modes, plus `group_vowels=True` to merge similar vowels. Dependencies: jamo, python-mecab-ko, konlpy, nltk. Apache-2.0, 273 stars, 21 commits. The README gives no accuracy figures. — [github.com/Kyubyong/g2pK](https://github.com/Kyubyong/g2pK)
- **g2pK rule coverage** (`g2pk/rules.txt`), 표준 발음법 articles:
  - 제5항 (ㅖ/ㅢ variants)
  - 제9–11항 (coda neutralization and cluster simplification)
  - 제12항 (ㅎ: aspiration, elision)
  - 제13–15항 (연음, resyllabification)
  - 제16항 (letter names)
  - 제17항 (구개음화)
  - 제18항 (비음화)
  - 제19–20항 (ㄹ→ㄴ and 유음화)
  - 제22항
  - 제23–28항 (경음화 variants)
  - 제29항 (ㄴ첨가)
  - 제30항 (사이시옷)

  — [g2pK rules.txt](https://raw.githubusercontent.com/Kyubyong/g2pK/master/g2pk/rules.txt)
- **g2pk** 0.9.4, released 2020-08-05, Apache-2.0 (last PyPI release). — [PyPI JSON](https://pypi.org/pypi/g2pk/json)
- **g2pkk** 0.1.2, 2022-09-05, Apache-2.0. A "cross-platform" fork that uses eunjeon on Windows and python-mecab-ko elsewhere. — [PyPI JSON](https://pypi.org/pypi/g2pkk/json); [PyPI page (search summary)](https://pypi.org/project/g2pkk/)
- **g2pkiwi** 0.1.0, 2022-10-26, Apache-2.0. A fork that uses Kiwi instead of MeCab. — [PyPI JSON](https://pypi.org/pypi/g2pkiwi/json)
- **Local test: g2pkiwi maintenance hazard.** g2pkiwi 0.1.0 fails to import with kiwipiepy 0.24.0 (`OSError: Cannot open required files for skipbigram model`, from `Kiwi(model_type="sbg")`). It fails with kiwipiepy 0.17.1 under NumPy 2 (`numpy.core.multiarray failed to import`). It **works with kiwipiepy 0.20.4**. On first import it also **downloads the NLTK cmudict over the network** to `~/nltk_data`. — local test, this session
- **KoG2P (Yejin Cho, 2017).** Rulebook-based (`rulebook.txt`), outputs a romanized phone set (e.g., p0/ph/pp, vowels ii/ee/qq/aa/…). Python 2.7/3, no external dependencies, **GPL-3.0**. The README has no rule list and no accuracy figures. — [github.com/scarletcho/KoG2P](https://github.com/scarletcho/KoG2P)
- **Supporting libraries:**
  - python-mecab-ko 1.3.7 (2024-07-14, BSD-3) — [PyPI](https://pypi.org/pypi/python-mecab-ko/json)
  - kiwipiepy 0.24.0 (2026-09-25, Apache-2.0) — [PyPI](https://pypi.org/pypi/kiwipiepy/json)
  - jamo 0.4.1 (2017, Apache-2.0) — [PyPI](https://pypi.org/pypi/jamo/json)
  - hangul-jamo 1.0.1 (2022, MIT) — [PyPI](https://pypi.org/pypi/hangul-jamo/json)
  - hgtk 0.2.1 (2023, Apache-2.0) — [PyPI](https://pypi.org/pypi/hgtk/json)
  - jamotools 0.1.10 (2018, GPL) — [PyPI](https://pypi.org/pypi/jamotools/json)
  - soynlp 0.0.493 (2019, GPLv3) — [PyPI](https://pypi.org/pypi/soynlp/json)
  - korean-romanizer 0.28.0 (2025, GPLv3+) — [PyPI](https://pypi.org/pypi/korean-romanizer/json)

**Standard pronunciation facts relevant to our errors**
- **ㅚ/ㅟ.** Under 표준 발음법 제4항 they are monophthongs, but 붙임 allows diphthong pronunciation. ㅚ pronounced as a diphthong equals [w]+ㅔ, so ㅚ≈ㅞ. — [국립국어원 온라인가나다, "단모음 'ㅚ'와 'ㅟ'의 허용 발음"](https://www.korean.go.kr/front/onlineQna/onlineQnaView.do?mn_id=&qna_seq=326554&pageIndex=1); 표준 발음법 index: [국립국어원](https://www.korean.go.kr/front/page/pageView.do?page_id=P000103&mn_id=95)
- **ㅕ/ㅖ/ㅢ.** The standard allows monophthongization for these, and 제5항 covers ㅖ. — [국립국어원 새국어소식, "이중 모음의 단모음화"](https://www.korean.go.kr/nkview/nknews/200103/32_5.htm)
- **ㄴ첨가 (제29항).** In compounds and derivatives, when the first element ends in a consonant and the next begins with 이/야/여/요/유, ㄴ is inserted ([니/냐/녀/뇨/뉴]). Some words allow both forms. — [국립국어원 search summary](https://www.korean.go.kr/front/page/pageView.do?page_id=P000103&mn_id=95) (verbatim article text not retrieved; the page body did not load)
- **ㅐ/ㅔ merger.** Acoustic data show that younger Seoul (and Kyungsang) speakers share a seven-vowel system "which result[s] from the merger of /e/–/ɛ/ in Seoul Korean". — [Lee & Jongman, J. Int. Phonetic Assoc. 2016 (PDF)](https://kuppl.ku.edu/sites/kuppl/files/documents/publications/Lee_Jongman_JIPA2016.pdf); see also [Sogang thesis on ㅔ/ㅐ merger](https://scholarworks.sogang.ac.kr/item/67e44c87-1899-4c64-90dd-23fad732fe43)

**Whisper's Korean error profile**
- In Whisper small/medium/large-v2/large-v3 on 54,536 NIKL Daily Conversation 2022 utterances, coda error rates were 2.67% (large-v3) to 4.53% (small). Phonologically motivated errors were 23–41% of errors. C2 simplification was dominant, followed by C1 simplification and aspiration merger; nasal assimilation and resyllabification were near zero. — [Yoon, Kwon, Han, Phonetics and Speech Sciences 2026, doi:10.13064/ksss.2026.18.1.055](https://doi.org/10.13064/ksss.2026.18.1.055)

**Jamo-level edit distance (Korean research)**
- Kang (2015) argues that syllable-level edit distance is inefficient for Korean because "two or more letter units make a Korean character". He proposes edit distance with consonant normalization and a normalization factor. The abstract page gives no specific costs or results. — [Kang, JIPS 11(4):573–582, doi:10.3745/JIPS.04.0018](https://doi.org/10.3745/JIPS.04.0018)

**Korean ASR post-correction papers (lexicon/phonetic/rule-based)**
- Han & Choi (2007) do post-processing correction with phoneme similarity. The similarity is the Bhattacharyya distance between monophone models (MFCC and LPC features). Error-flagged eojeol are corrected, then syllables are restored and morphological analysis is re-run. Recognition improved by 7.5% (MFCC) and 5.3% (LPC). — [Han & Choi, J. Korea ITS 6(3):77–86, 2007](https://www.koreascience.or.kr/article/JAKO200709906244055.page)
- Kim & Park (2012) present a rule-based ASR error corrector for mobile use. Rules are auto-extracted from a training corpus, applied by longest match without morphological analysis, and produce one candidate per error point. Eojeol precision +5.27%, recall +5.60%. — [J. Korea Soc. Comput. Inf. 17(10):25–33, doi:10.9708/jksci/2012.17.10.025](https://koreascience.or.kr/article/JAKO201233355899719.pub)
- Oh (2013) uses phoneme similarity rate plus reliability for error compensation and reports "98.3% recognition, 95.5% error compensation". The setup is thinly described, so treat these figures with caution. — [Journal of Digital Convergence 11(6):137–142](https://www.earticle.net/Article/A200610)
- A KCI conference paper (2014) modifies edit distance to account for pronunciation variation, for correcting misrecognized proper nouns. Reported P@3 is 55% better than the comparison model. **Snippet-level only**: the full text could not be retrieved (koreascience.kr timed out or failed DNS), so the method details are unverified. — [koreascience CFKO201408355727254](https://www.koreascience.kr/article/CFKO201408355727254.pdf)
- Im, Lee, Lee, Kim (POSTECH, HCLT 2023) recognize speech at jamo level (51 jamo, wav2vec2.0, KsponSpeech) and then merge jamo into syllables with a Transformer instead of rules. This gives relative CER −17.2% and WER −13.1% (eval-clean) versus rule-based merging. It is neural and not lexicon-driven, but it shows that jamo-level representations work for Korean ASR correction. — [제35회 한글 및 한국어 정보처리 학술대회 (2023)](https://www.koreascience.kr/article/CFKO202306643317907.pdf)

**Local test: our examples**

Distances are Levenshtein over jamo with silent onset ㅇ dropped. "G2P" means g2pkiwi with `descriptive=True, group_vowels=True`.

| Reference | ASR output | Raw jamo distance | G2P(ref) | G2P(hyp) | G2P jamo distance | Ref jamo length |
|---|---|---|---|---|---|---|
| 간염 | 간념 | 1 | 가념 | 간념 | 1 | 5 |
| 패혈증 | 폐혈증 | 1 | 페혈증 | 페혈증 | **0** | 8 |
| 궤양 | 괴양 | 1 | 괴양 | 괴양 | **0** | 4 |
| 원핵 | 원액 | 1 | 원헥 | 워넥 | 1 (ㅎ deletion) | 5 |
| 보체 | 보처 | 1 | 보체 | 보처 | 1 (ㅔ/ㅓ) | 4 |
| 엔테로콜리티카 | 엔트로콜리티카 | 1 | (unchanged) | (unchanged) | 1 (ㅔ/ㅡ) | 15 |
| 간염 | **감염** (real word) | 1 | 가념 | 가몀 | 1 | 5 |
| B형 간염 | B형 감염 | 1 | 비형 가념 | 비형 가몀 | 1 | 10 |
| 독소 | 독서 (control, both real words) | 1 | 독쏘 | 독써 | 1 | 5 |

— local test, this session

- In prescriptive mode, g2pkiwi output 간염→가념 (matching the standard [가ː념], i.e., ㄴ첨가), 감염→가몀, 비말감염→비말가몀, 식염→시겸, 신라→실라, 국물→궁물, 같이→가치. Option effects:
  - `group_vowels` alone: 패→페 and 궤→괴, but 폐 is left unchanged.
  - `descriptive` alone: 폐→페.
  - Both flags are needed for 패혈증≡폐혈증.

  — local test, this session
- **Acronyms are not converted.** g2pkiwi left `CMV` and `VZV` unchanged (all modes). — local test, this session

### Inferences
- **G2P helps for vowel-merger errors.** Errors from ㅐ/ㅔ/ㅖ and ㅚ/ㅙ/ㅞ mergers (패혈증→폐혈증, 궤양→괴양) become exact matches after G2P with `descriptive=True, group_vowels=True`. This class can be corrected at high confidence *if* the hypothesis is a non-word (Q3).
- **Phonetic spellings need a degemination rule.** "Phonetic spelling" errors (간염→간념) come from Whisper writing the surface ㄴ-inserted form while also keeping the orthographic coda ㄴ. G2P(간념) stays 간념, so distance stays 1. A cheap fix is to treat coda-ㄴ + onset-ㄴ as equivalent to onset-ㄴ (degemination) at cost 0 when comparing phone strings.
- **ㅎ weakening needs a low-cost deletion.** g2pK does not model initial ㅎ weakening after sonorants (원핵 stays 원헥). This is a casual-speech phenomenon rather than a 표준 발음법 rule. Assigning a low cost to "ㅎ deletion after ㄴ/ㅁ/ㅇ/ㄹ/vowel" in a weighted jamo DP would bring 원핵/원액 to near zero.
- **Vowel confusions stay at distance 1.** 보체→보처 (ㅔ/ㅓ) and 엔테로→엔트로 (ㅔ/ㅡ in a loanword) are acoustic vowel confusions that no G2P normalizes. A per-vowel confusion cost table estimated from our own correction log is the deterministic option.
- **Normalize by length.** One jamo out of 15 (엔테로콜리티카) is a much safer match than one out of 4 (보체). Thresholds should be length-normalized, and short terms (≤2 syllables, roughly ≤5 jamo) should be treated as high-risk, as 독소/독서 shows.
- **Acronyms need a letter-name layer.** CMV→cnv and VZV→VGV need a deterministic expansion of Latin letters into Korean letter-name pronunciations before matching: C=씨, M=엠, N=엔, V=브이, G=지, and Z with alternates 제트/지/즈. Then CMV=씨엠브이 vs cnv=씨엔브이 is distance 1 (coda ㅁ/ㄴ, nasal place). VZV read with Z="지" gives 브이지브이 ≡ VGV 브이지브이 at distance 0. Case-folding (cnv→CNV) should happen before lookup. This layer is our own design; no Korean source describing it was found.
- **Operational note for an offline server.** Pin `kiwipiepy==0.20.4` if g2pkiwi is used, or use g2pk/g2pkk with python-mecab-ko. Pre-provision `nltk_data/corpora/cmudict`, because g2pK-family packages download it at first import. Since g2pK is unmaintained, the cleanest long-term option is to **vendor** the rule table (Apache-2.0 permits this with notice) or to write a small rule subset. Only 연음, ㄴ첨가, 비음화, 유음화, 경음화, vowel grouping and ㅎ handling matter for matching. KoG2P is GPL-3.0, which can matter if the code is redistributed.

### Gaps
- No published accuracy (phone or word error rate) was found for g2pK, g2pkk, g2pkiwi or KoG2P on a Korean pronunciation test set.
- The verbatim text of 표준 발음법 제4·5·12·29항 was not retrieved (the 국립국어원 page body did not load). The rule content above comes from the g2pK rules file, 국립국어원 Q&A pages and search summaries.
- No Korean study was found of Whisper's errors on *technical/medical terms* specifically. The only Whisper-Korean error analysis found concerns coda clusters in conversation.
- I did not verify that soynlp exposes a `jamo_levenshtein` function (its license is GPLv3 per PyPI).

---

## Q3. Telling true errors apart from legitimate words: non-word gating, context rules, n-gram LM scoring, domain co-occurrence

### Takeaway
Detection, not candidate selection, is the hard part. Trigram LMs are good at choosing between a word and its close variant once a position is suspected, but they create more errors than they fix if every word is treated as suspect. The safe deterministic design has three tiers:
1. **Non-word hypotheses** (the span is in no general dictionary) at small phonetic distance from a glossary term: auto-correct and log.
2. **Real-word hypotheses** (감염, 원액): correct only when an explicit context pattern or a domain-LM likelihood ratio fires, as in "B형/C형 + 감염 → 간염".
3. Everything else: flag for review only.

A morphological analyzer cannot serve as the non-word test, because Kiwi tags unknown strings such as 간념 and 보처 as ordinary nouns (NNG).

### Cited Findings
- "Detection is the central problem in real-word spelling correction". Trigram models discriminate well between the intended word and a close variant, but "trigram models cannot reliably find true errors without introducing many more, at least not when used in the obvious sequential way". — [Wilcox-O'Hearn, arXiv:1408.3153 (2014)](https://arxiv.org/abs/1408.3153)
- Wilcox-O'Hearn & Hirst (2008) extended this with multiple corrections per sentence and fixed-length windows, building on Hirst & Budanitsky (2005), and evaluated on WSJ 1987–89. — [search summary; arXiv:1408.3153](https://arxiv.org/abs/1408.3153); see also [arXiv:2302.04096](https://arxiv.org/pdf/2302.04096)
- Unlike traditional ASR spellchecking, contextual spelling correction targets terms that are often OOV or spelling variants, "and language models cannot help much with that". — [SpellMapper §1](https://arxiv.org/abs/2306.02317)
- **FP controls in published correctors** (all reproducible as deterministic gates):
  - CSC leaves a position unchanged when the top similarity probability is below a threshold s_o, and adds an NER detector that decides whether the hypothesis contains any biasable term. On a "Random8k" anti-context set, WER was unchanged (8.8 → 8.8). — [CSC, arXiv:2203.00888](https://arxiv.org/abs/2203.00888)
  - DANCER uses an entity-rejection module to avoid replacing correctly recognized entities. It "may slightly decrease the NE recall rate" but significantly reduces CER on non-entity parts. — [DANCER §4.1](https://arxiv.org/abs/2403.17645)
  - SpellMapper filters retrieved candidates by n-gram *coverage* of the phrase, and adding subword context "reduce[s] false positives by a quarter". — [SpellMapper §3.2–3.3](https://arxiv.org/abs/2306.02317)
- SpellMapper's FP example: "an artist's right to fail => an artist's reilly to fail" (a user phrase hallucinated into unrelated text). The paper's precision is defined as better/(better+fp). — [SpellMapper §3.4](https://arxiv.org/abs/2306.02317)
- **Local test: Kiwi does not give a usable non-word signal.** kiwipiepy 0.24.0 tokenized 간념 → `간념/NNG`, 보처 → `보처/NNG` and 엔트로콜리티카 → `엔트로콜리티카/NNG`, which are the same tag as real nouns. 폐혈증 was split into `폐/NNG 혈증/NNG`, and 괴양 into `괴/VV 양/NNG`. — local test, this session
- **Local test: Kiwi typo correction does not fix domain errors.** With `typos="basic"` or `"basic_with_continual"`, even after `add_user_word` for 패혈증/궤양/간염/원핵/보체/엔테로콜리티카, none of the 8 test sentences were corrected (default `typo_cost_threshold`). — local test, this session. Kiwi's typo modes target generic typos (외않됀대→왜 안 되는데), linking errors (사무시레서→사무실에서) and lengthening (지이인짜→진짜), and over-correction is limited by cost thresholding (default 2.5). — [github.com/bab2min/kiwipiepy](https://github.com/bab2min/kiwipiepy)

### Inferences
- **Non-word test.** "Not in a general dictionary" must come from an explicit word list, such as a 표준국어대사전/우리말샘 headword dump, a frequency list from a large general corpus, or the vocabulary of a general n-gram LM. Morphological tags will not do (see the local test). The word list is a versioned artifact and should be logged with every run for auditability. The licensing of 우리말샘 and 표준국어대사전 dumps was not researched here.
- **Worked decision logic for the examples:**
  - 간념, 폐혈증, 괴양, 보처, 엔트로콜리티카, VGV and cnv are presumably non-words. If they are, then phonetic distance ≤ 1 (normalized ≤ ~0.2) to exactly one glossary term qualifies for auto-correction, with a mandatory before/after log.
  - 감염 and 원액 are real words, so they never auto-correct on phonetics alone.
  - "B형 감염 → B형 간염" needs a context rule: left context ∈ {A형, B형, C형, D형, E형, 바이러스성, 만성, 급성…} + 감염 → 간염. The rule can be generated from the glossary itself by finding glossary terms that contain the corrected form (B형 간염, C형 간염).
  - "비말감염" is safe for three reasons: it is itself a glossary/dictionary word, the left context does not match, and Korean longest-match tokenization treats it as one unit (Kiwi: `비말감염/NNG`).
- **Domain LM ratio.** For real-word candidates, a small n-gram LM trained on domain text (the textbook or lecture slides the professor already has, which keeps data local) can compute log P(context with candidate) − log P(context with original). Apply it *only* at positions already flagged by phonetic retrieval, never as a sweep, following Wilcox-O'Hearn. The threshold should be fixed and logged.
- **Co-occurrence.** If a lecture contains the correct term elsewhere (for example, 간염 already appears correctly in the same transcript), that is cheap, auditable evidence. Requiring "the candidate term appears correctly at least once in the same lecture, or appears in that week's glossary subset" would cut false positives.
- **Ambiguity rule.** If two or more glossary terms tie within the threshold (homophone-dense neighbourhood, the DANCER failure mode), do not correct; flag instead.

### Gaps
- No Korean-specific evaluation of real-word vs non-word gating for ASR technical-term correction was found.
- Open-data availability and licenses for a Korean general headword list suitable as the non-word dictionary were not researched in this session.

---

## Q4. Confidence signals from Whisper itself: word-level probabilities as an error detector, and calibration evidence

### Takeaway
faster-whisper's `Word.probability` is the **mean** of the word's token probabilities. Research on Whisper finds raw softmax confidence only moderately predictive of word errors (AUC-ROC about 0.69–0.81 on English/European test sets, using the better *min* aggregation), and overconfident, especially in noise. Low probability is therefore a useful *trigger* that raises the prior that a span is wrong. High probability does not prove a span is right, so it should not veto a lexicon match. No Korean calibration study was found.

### Cited Findings
- faster-whisper computes word probabilities as `word_probabilities = [np.mean(text_token_probs[i:j]) for i, j in zip(word_boundaries[:-1], word_boundaries[1:])]`, with `text_token_probs` taken from `self.model.align()`. Segment `avg_logprob` = cumulative logprob × seq_len^length_penalty / (seq_len + 1). `no_speech_prob` is returned by the model. — [SYSTRAN/faster-whisper transcribe.py](https://raw.githubusercontent.com/SYSTRAN/faster-whisper/master/faster_whisper/transcribe.py)
- faster-whisper 1.2.1 (2025-10-31) is MIT licensed. — [PyPI](https://pypi.org/pypi/faster-whisper/json)
- **Raw softmax baseline for word confidence.** The minimum of token softmax probabilities "outperform[s] mean, sum, product, and maximum" aggregations. Raw softmax "often suffers from overconfidence issue, wherein the model tends to output high probabilities for words that are actually incorrect". — ["Adopting Whisper for Confidence Estimation", arXiv:2502.13446 (2025)](https://arxiv.org/abs/2502.13446) (author list not retrieved in this session)
- **Raw-softmax error detection (Whisper-large hypotheses), min-aggregation, AUC-ROC:** LibriSpeech-clean 0.783, LS-other 0.809, VoxPopuli 0.736, CHiME-6 0.694, FLEURS 0.764, MLS-fr 0.711, MLS-it 0.728, MLS-pt 0.794. AUC-PR with errors as positives was 0.204–0.533. A fine-tuned C-Whisper-large confidence model reaches AUC-ROC 0.773–0.922. Training used Common Voice 18 English (~1,700 h); there are no Korean results and no code release is mentioned. — [arXiv:2502.13446 Table I](https://arxiv.org/html/2502.13446v1)
- **Overconfidence in noise (Whisper-medium-en).** At low SNR (−18 to −5 dB), 11.1% of tokens were wrong yet had confidence > 0.7, with ECE 0.086. Selective temperature scaling reduced ECE to 0.036 (−58%) and the overconfident mass to 6.6%. The authors advise against relying on raw softmax for error flagging in noise. — [arXiv:2509.07195 (2025)](https://arxiv.org/html/2509.07195v1)
- Whisper's top-token likelihood and confidence are lower for lower-resource languages (cross-language probe of the sub-token decoder). — [Beyond WER: Probing Whisper's Sub-token Decoder, EMNLP 2025 (arXiv:2509.25516)](https://arxiv.org/html/2509.25516v1) (search-summary level; Korean-specific numbers not retrieved)

### Inferences
- **Make the aggregation change auditable.** faster-whisper reports the *mean* of token probabilities, but the best raw aggregation in the Whisper study was the *minimum*. A multi-token rare term (Korean technical words usually split into several byte-BPE tokens) with one very uncertain token is diluted by the mean. Recomputing a min-aggregate would require access to per-token probabilities, which `Word` does not expose. Patching or forking would be a deliberate, auditable change and should be flagged as such.
- **Use probability in the score, not as a veto.**
  - Recommended deterministic use: a low word probability (threshold calibrated on our own hand-checked lectures) lowers the bar for accepting a phonetic match, for example by allowing distance 1 on a short term.
  - A high probability alone should not block a correction for a non-word hypothesis like 간념, given the documented overconfidence.
  - Record the probability value in the correction log for audit.
- **Calibrate locally.** Because no Korean calibration data exists, the threshold should be estimated from a small labelled sample of our own transcripts (Whisper output vs corrected text). Report AUC-ROC and precision at the chosen threshold, which can be done with plain static code.

### Gaps
- No calibration or error-detection study of Whisper word probabilities on Korean, or on lecture speech, was found.
- The exact author list of arXiv:2502.13446 was not verified.
- It is unverified whether faster-whisper exposes per-token probabilities through any public API, beyond `Word.probability` and segment `avg_logprob`.

---

## Q5. Contextual spelling correction (non-LLM seq2seq/tagging correctors with biasing lists): inputs, training needs, gains

### Takeaway
Neural contextual spelling correctors (Microsoft CSC/FCSC, NVIDIA SpellMapper, DANCER) achieve large relative WER reductions on biased-term test sets: about 50% for CSC on names, about 39% on medical, and 6–21% for SpellMapper. All three, however:
- need millions of synthetic training pairs, usually made by TTS → ASR;
- are English or Mandarin only;
- are not static code.

Their transferable parts are deterministic: candidate filtering by normalized edit distance or n-gram mapping, confidence thresholds, entity rejection, coverage filters, and evaluation metrics that count only *changed* positions. For a Korean, offline, audit-first pipeline, those components carry over; the trained models do not.

### Cited Findings
- **Microsoft CSC (TASLP 2022).**
  - Architecture: seq2seq spelling correction on top of E2E ASR with a shared context encoder, in AR and NAR variants. NAR is 43.2% smaller and 2.1× faster.
  - Training: 1M synthetic names (from 48k name words) plus 6M proper nouns (NELL, Yago); audio from a 363-speaker multi-locale English TTS; hypotheses from RNN-T; 512 sentence patterns plus 26M in-house general scripts; 20% general utterances without context phrases.
  - Results: Name set 24.1 → 11.8% WER (−51.0%; −55.6% with ASR biasing); Text Editor 14.6 → 7.2%; Medical 14.0 → 8.6% (−38.6%); anti-context Random8k 8.8 → 8.8%.
  - English only; no code.

  — [Wang, Liu, Li, Miljanic, Zhao, Khalil, arXiv:2203.00888](https://arxiv.org/abs/2203.00888); [ar5iv full text](https://ar5iv.labs.arxiv.org/html/2203.00888)
- **SpellMapper (Interspeech 2023).**
  - Inputs: 10–15-word ASR fragments plus a user vocabulary of "up to several thousand words or phrases", reduced to top-10 candidates.
  - Model: 67M-parameter BERT backbone (TinyBERT 6L-768D), char-level, trained on 10M synthetic examples on 8 GPUs.
  - Results: SWC CTC 6.69 → 5.26% WER (ideal 4.46); SPGI 5.88 → 5.50; UserLibri 3.35 → 2.89. Diff-only recall 53.2–67%, precision 77.6–88.7%. It recovers 49–68% of the "ideal" improvement.
  - The authors note "many false positives" and leave "languages with highly inflectional morphology" to future work.
  - Code in NeMo.

  — [arXiv:2306.02317](https://arxiv.org/abs/2306.02317); [ISCA archive](https://www.isca-archive.org/interspeech_2023/antonova23_interspeech.html); [NeMo docs](https://docs.nvidia.com/deeplearning/nemo/user-guide/docs/en/main/nlp/spellchecking_asr_customization.html)
- **SpellMapper evaluation design.** The "ideal spellchecker" aligns reference and hypothesis and counts a fragment as correctable if the reference fragment is in the vocabulary. This gives a lower bound on achievable WER. Precision and recall are computed only on positions where something changed (80–85% of user-phrase occurrences were already correct in both). — [SpellMapper §3.4](https://arxiv.org/abs/2306.02317)
- **DANCER (LREC-COLING 2024).** It needs a trained corrupted-entity detector (Transformer BIO tagger), a dense retriever (DPR-style) and an entity-description memory. Results are in Q1. Code is on GitHub. — [arXiv:2403.17645](https://arxiv.org/abs/2403.17645)
- **Training-free correction is attractive for this reason.** CSC "does not require retraining of the acoustic model". Shallow fusion "is prone to false positives and is not applicable if the needed path is not present in the graph". — [SpellMapper §1–2](https://arxiv.org/abs/2306.02317)

### Inferences
- **Training a Korean CSC/SpellMapper would be a separate project**, not a configuration step. It would need a Korean TTS, large synthetic pairs and GPU training, and it adds a non-auditable model in the loop. For quote-integrity requirements, the deterministic pipeline (Q1–Q3 gates) is the better fit. A neural reranker could later be added *only as a reviewer-suggestion source*, never as an auto-apply step.
- **Borrow SpellMapper's evaluation protocol as-is.** Ideal-WER bound, diff-only precision and recall, and a split of candidate-retrieval recall versus final recall. Together these make our deterministic corrector's performance auditable per lecture and per term.
- **Build a Korean mapping table from our own data.** The SpellMapper approach of deriving an n-gram corruption table (Table 1-style frequencies) from aligned (reference, ASR) pairs could run on our own accepted corrections over time. That would yield a Korean, Whisper-specific, data-derived confusion-cost table (e.g., ㅔ→ㅓ, ㅐ→ㅖ, ㅎ→∅, 엠→엔) for the weighted jamo DP. It would be static, versioned and explainable.

### Gaps
- No Korean contextual spelling correction model or benchmark (public code or data) was found.
- FastCorrect and SoftCorrect, the non-autoregressive ASR correctors from Microsoft, were not researched in this session. Their gains and training needs are unverified here.
- No non-LLM, non-English CSC evaluation with a list of several thousand technical terms was found. The closest are SpellMapper (up to 1,341 phrases per user, English) and CSC (K_f = 100 after filtering, English).
