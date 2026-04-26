# SafeSurveil-AIxBio Literature Audit

Date: 2026-04-26

Purpose: record the Consensus-backed literature pass used for the hackathon report. Papers cited in the report should come from this audit or be clearly labeled as web-only/current source material.

## Search Summary

Detected Consensus result cap: approximately 20 papers per search.

| # | Query Focus | Filters | Papers Returned | Status |
|---|---|---|---|---|
| 1 | AMR genomic surveillance, ML, explainable decision support, grounded LLMs | year_min=2015, SJR<=2, reviews/observational | 19 | Success |
| 2 | WGS AMR surveillance, databases, AMRFinder/CARD/ResFinder | year_min=2015, SJR<=2, reviews/observational | 20 | Success |
| 3 | ML AMR prediction from WGS/AST, validation, calibration, interpretability | year_min=2017, SJR<=2, reviews/observational | 19 | Success |
| 4 | AI antimicrobial stewardship CDSS, safety, interpretability | year_min=2018, human, SJR<=2, reviews/observational | 20 | Success |
| 5 | LLMs for antibiotic stewardship and clinical decision support safety | year_min=2023, human, SJR<=2, reviews/observational | 20 | Success |
| 6 | Biomedical knowledge graphs, evidence graphs, XAI, provenance | year_min=2018, SJR<=2, reviews/observational | 20 | Success |
| 7 | Biosecurity AI governance, audit, traceability, dual-use risk | year_min=2018, SJR<=2, reviews/observational | 20 | Success |
| 8 | AMRFinderPlus and AMR gene detection tooling | year_min=2018, SJR<=2, reviews/observational | 20 | Success |
| 9 | Mash/genomic distance, novelty, genomic epidemiology surveillance | year_min=2015, SJR<=2, reviews/observational | 20 | Success |
| 10 | BioReason-style biological reasoning traces | year_min=2023, SJR<=2, reviews/observational | 20 | Success |

Counts:

- Searches executed: 10.
- Searches successful: 10.
- Searches failed after retry: 0.
- Unique papers received: not fully deduplicated; at most 198 raw returned records before cross-search duplicates.
- Papers selected for likely citation: 18 core papers plus 2 web/current BioReason sources.

## Priority Citation Set

### Genomic AMR Surveillance and WGS Evidence

1. Boolchandani et al. (2019). "Sequencing-based methods and resources to study antimicrobial resistance." Nature Reviews Genetics. Consensus: https://consensus.app/papers/details/435fbf72968c509e9a860a6c87d62175/?utm_source=unknown
2. Hendriksen et al. (2019). "Using Genomics to Track Global Antimicrobial Resistance." Frontiers in Public Health. Consensus: https://consensus.app/papers/details/63ca2e3f2d9351809e2a67a5125cb606/?utm_source=unknown
3. Oniciuc et al. (2018). "The Present and Future of Whole Genome Sequencing (WGS) and Whole Metagenome Sequencing (WMS) for Surveillance of Antimicrobial Resistant Microorganisms and Antimicrobial Resistance Genes across the Food Chain." Genes. Consensus: https://consensus.app/papers/details/05f276609d5255abb0f4d6afd48851b5/?utm_source=unknown
4. Collineau et al. (2019). "Integrating Whole-Genome Sequencing Data Into Quantitative Risk Assessment of Foodborne Antimicrobial Resistance." Frontiers in Microbiology. Consensus: https://consensus.app/papers/details/933edec6750e535fa356054fabf4c17f/?utm_source=unknown
5. Mahfouz et al. (2020). "Large-scale assessment of antimicrobial resistance marker databases for genetic phenotype prediction." Journal of Antimicrobial Chemotherapy. Consensus: https://consensus.app/papers/details/656f5d8c9ae5558e8d3abbb81aaeff63/?utm_source=unknown
6. Su et al. (2018). "Genome-Based Prediction of Bacterial Antibiotic Resistance." Journal of Clinical Microbiology. Consensus: https://consensus.app/papers/details/06e5710440fa5f90825a2a79cd48ffc6/?utm_source=unknown

### ML and Antimicrobial Stewardship Decision Support

7. Peiffer-Smadja et al. (2020). "Machine learning for clinical decision support in infectious diseases." Clinical Microbiology and Infection. Consensus: https://consensus.app/papers/details/72fce91a95125f52a36c655e1406d4e9/?utm_source=unknown
8. Ardila et al. (2024). "Integrating whole genome sequencing and machine learning for predicting antimicrobial resistance in critical pathogens." PeerJ. Consensus: https://consensus.app/papers/details/dd18acba2ab1532982dc97a67e2ec704/?utm_source=unknown
9. Ardila et al. (2025). "Machine learning for predicting antimicrobial resistance in critical and high-priority pathogens." PLOS One. Consensus: https://consensus.app/papers/details/3c1bc3a411a65ac5abfd9bf035c15316/?utm_source=unknown
10. Pinto-de-Sa et al. (2024). "Brave New World of Artificial Intelligence: Its Use in Antimicrobial Stewardship--A Systematic Review." Antibiotics. Consensus: https://consensus.app/papers/details/4b1d1024b494588cb81b97afa00e80a6/?utm_source=unknown
11. AlGain et al. (2025). "Can we rely on artificial intelligence to guide antimicrobial therapy?" Antimicrobial Stewardship & Healthcare Epidemiology. Consensus: https://consensus.app/papers/details/a9e8e10b1f805a0a888faef3767e2ff2/?utm_source=unknown

### LLMs, RAG, XAI, and Trustworthy Healthcare AI

12. Antonie et al. (2025). "The Role of ChatGPT and AI Chatbots in Optimizing Antibiotic Therapy." Antibiotics. Consensus: https://consensus.app/papers/details/d66b7ce37dd2588c8418dcb1c8a6895b/?utm_source=unknown
13. Park et al. (2024). "Assessing the research landscape and clinical utility of large language models." BMC Medical Informatics and Decision Making. Consensus: https://consensus.app/papers/details/89c0222087fa5c39b88eb06a4d71df0d/?utm_source=unknown
14. Amugongo et al. (2025). "Retrieval augmented generation for large language models in healthcare." PLOS Digital Health. Consensus: https://consensus.app/papers/details/9d1904c2383e5dcc954c87f5ad10a66f/?utm_source=unknown
15. Antoniadi et al. (2021). "Current Challenges and Future Opportunities for XAI in Machine Learning-Based Clinical Decision Support Systems." Applied Sciences. Consensus: https://consensus.app/papers/details/d3f76f92a3575f96bec1e0c22c80a9f3/?utm_source=unknown
16. Budhdeo et al. (2023). "Scoping review of knowledge graph applications in biomedical and healthcare sciences." Wellcome Open Research. Consensus: https://consensus.app/papers/details/9c5d9984fda35ff3a299fa0a726f1607/?utm_source=unknown
17. Rajabi and Etminani (2022). "Knowledge-graph-based explainable AI: A systematic review." Journal of Information Science. Consensus: https://consensus.app/papers/details/affdd66df0055d2596d7f155c3857fc4/?utm_source=unknown

### Biosecurity and Governance

18. Undheim (2024). "The whack-a-mole governance challenge for AI-enabled synthetic biology." Frontiers in Bioengineering and Biotechnology. Consensus: https://consensus.app/papers/details/d13cdf4c37085be28493ea131ec8a1ca/?utm_source=unknown
19. Elgabry et al. (2024). "Cyber-biological convergence: a systematic review and future outlook." Frontiers in Bioengineering and Biotechnology. Consensus: https://consensus.app/papers/details/f773095e6925525a83eddd97ecb678de/?utm_source=unknown

### Current BioReason Context From Web Search

20. Fallahpour et al. (2025). "BioReason: Incentivizing Multimodal Biological Reasoning within a DNA-LLM Model." arXiv. Web: https://arxiv.org/abs/2505.23579
21. Fallahpour et al. (2026). "BioReason-Pro: Advancing Protein Function Prediction with Multimodal Biological Reasoning." BioRxiv / Arc Institute article. Web: https://arcinstitute.org/news/bioreason-pro

## Report Framing Notes

- The report should not claim clinical validation or broad AMR predictive superiority.
- The strongest novelty claim is a runtime trust architecture for AMR genomic triage: execution gate, deterministic reasoning trace, evidence graph, grounded copilot validation, and audit page.
- SafeSurveil is best framed as a defensive surveillance and analyst-review system, not a prescribing engine.
- LLM literature supports human oversight, retrieval/evidence grounding, refusal behavior, and explicit evaluation rather than autonomous clinical advice.
- WGS/AMR literature supports genomic surveillance value while motivating visible uncertainty around database choice, genotype-phenotype discordance, and standardization gaps.
