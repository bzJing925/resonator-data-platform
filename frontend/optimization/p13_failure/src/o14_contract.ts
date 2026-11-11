export const EXPECTED_CANDIDATE_IDS = [
  "P1200_peak_qp_p3_EG0.9750000000000001_FL1.5_A5500_PF0",
  "P1201_peak_qp_p6c_EG3.0_FL2.0_A5500_PF0",
  "P1202_peak_k2eff_pct_p3_EG1.3_FL0.0_A5500_PF0",
  "P1203_peak_k2eff_pct_p6c_EG3.0_FL0.025_A5500_PF0",
  "P1204_joint_p3_EG1.25_FL0.17500000000000002_A5500_PF0",
  "P1205_joint_p3_EG1.2750000000000001_FL0.275_A5500_PF0",
  "P1206_joint_p6c_EG3.0_FL1.875_A5500_PF0",
  "P1107_P11_EG2.5_FL0.0_A5500_PF0",
  "P1108_P11_EG2.5_FL0.5_A5500_PF0",
  "P1109_P11_EG3.0_FL0.0_A5500_PF0",
  "P1110_P11_EG3.0_FL0.5_A5500_PF0",
  "P1111_P11_EG1.25_FL1.25_A5500_PF0",
  "P1112_P11_EG1.25_FL1.5_A5500_PF0",
  "P1113_P11_EG1.25_FL1.75_A5500_PF0",
  "P1114_P11_EG1.0_FL1.375_A5500_PF0",
  "P1115_P11_EG1.0_FL1.625_A5500_PF0",
  "P1116_P11_EG2.0_FL0.25_A5500_PF0",
  "C17_negative_control_EG3.5_FL0.0_A5500_PF0",
  "C18_negative_control_EG1.0_FL2.5_A5500_PF0",
  "C19_negative_control_EG0.0_FL1.5_A700_PF1",
] as const;

export const EXPECTED_MODELS = ["P3", "P6C"] as const;
export const EXPECTED_METRICS = ["k2eff_pct", "qs", "qp", "bodeq_fitted"] as const;
export const C19_ID = "C19_negative_control_EG0.0_FL1.5_A700_PF1";
export const C19_STRUCTURE_ID = "s_c454ac7ec8a2a7f8";

export const SOURCE_SHA256 = {
  P12_CANDIDATES: "5721d619d93811a96e3b623f63145f4af060de1ff525d07fdd672c2d2fea6042",
  P3_STRUCTURES: "426d844b4854c95593e3e9b621637d12f42eb4422f6ad75b8e6e67a86d837000",
} as const;

export const O14_SHA256 = {
  "candidates.csv": "32eedf2acc5c54508d16b41ab79522df1d6ebcca95648580d38f627d404fc539",
  "metrics.csv": "10b281c30b530b62d8d34ba8361175031133a09d70cf4d8fc137d0f14175fbb7",
  "decision.json": "b93a563a354d7914db7755a9328b1367fd82547130e61b1f5afcc3f49692bf76",
} as const;

export const EXPECTED_REGISTERED_INPUTS = [
  ["A4_VALIDATOR", "/Users/jingbozuo/Projects/aln-data-backend-ml/backend/scripts/run_optimization_p13_failure_contract.py", 19894, "67fa1c1fc86867a1a233d73b42da9c03c4f4fa4e21c8c004effb2fc53b9be135"],
  ["H04_CONTRACT", "/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p13_20261027_r01/resume_20261029_r01/contracts/failure_delivery_contract.md", 11463, "f91c816789e54360472acf4961b958cbf236fb8cf61a8bfa9d0d7d7ec2a004c5"],
  ["H04_CONTRACT_TEST", "/Users/jingbozuo/Projects/aln-data-backend-ml/backend/tests/ml/test_optimization_p13_failure_contract.py", 11929, "b4edf240907de0b3a611b666c7b669ea3d6196762e0016cec62ee1d11266bf0b"],
  ["H04_MANIFEST", "/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p13_20261027_r01/resume_20261029_r01/contracts/artifact_manifest.json", 31677, "0c1815085acda6f40e6ca9c98c4b28408e1514dbbb284e67905ac452dcda2486"],
  ["H04_SCHEMA", "/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p13_20261027_r01/resume_20261029_r01/contracts/failure_delivery_schema.json", 25468, "6149b6bdc4ae78adc302f246c15593b6c7c48692b00d21262fbb233abbeb9d4f"],
  ["H05_MANIFEST", "/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p13_20261027_r01/resume_20261029_r01/signoff/h05_artifact_manifest.json", 5192, "ec174b9552ad7493f812186374c8c42caabdc2bcaceeff797d21940b13e13499"],
  ["O09_ARTIFACT_MANIFEST", "/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p13_20261027_r01/selection/artifact_manifest.json", 2474, "0c05bc47ad3b748f6ca4931afa89f602ad94b3b5647bda66c978f4c654498e76"],
  ["O09_ELIGIBLE_MODELS", "/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p13_20261027_r01/selection/eligible_models.json", 504, "1ef60bb937b8d95011a05284412f14836324bdd6fb9038b33534c98f555a61c1"],
  ["O09_SELECTION", "/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p13_20261027_r01/selection/selection.json", 11634, "ca034c07d87db0f9980b75b195f0d1ac44fc7e77662f1f03c153a9f6540e1de4"],
  ["O10_ARTIFACT_MANIFEST", "/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p13_20261027_r01/evaluation/t11_artifact_manifest.json", 5628, "b364663a5becf4e5c151f0184b073c016c293bcd85b5e2e689a1b101ac45704c"],
  ["O10_RETROSPECTIVE_REPORT", "/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p13_20261027_r01/evaluation/retrospective_report.json", 3974, "eca22ede5edf6f375ec7f927e7ed6ee982da9cfa1debd9c9f1e17e45bc17ebe8"],
  ["O11_ARTIFACT_MANIFEST", "/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p13_20261027_r01/search/t12_artifact_manifest.json", 11028, "f9f264336f630859661ea5cffef260e5429ef869d44f4220e61064fa7b78a2e3"],
  ["O11_GRID_PREDICTIONS", "/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p13_20261027_r01/search/grid_predictions.csv", 18261, "98aa6922bc2522b8ed29f376d7360052ac4eadfca581cb8b597b0a9cc2803ecb"],
  ["O11_SEARCH_PROTOCOL", "/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p13_20261027_r01/search/search_protocol.json", 3127, "1d264022c8bc14c7e40c00aeadf976639c5a443da856a75de393305f58b1182e"],
  ["O12_ARTIFACT_MANIFEST", "/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p13_20261027_r01/search/t13_artifact_manifest.json", 14736, "153488eba2ee3b1cf09dc9fa76a3197ec6a1818889ba03b54394050f21571c6a"],
  ["O12_SHORTLIST", "/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p13_20261027_r01/search/shortlist.csv", 21937, "5fae01108a757bf594da7e173ee6bf2a5b079e3b454bf0929b82db0e27a3fa1f"],
  ["O13_ARTIFACT_MANIFEST", "/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p13_20261027_r01/robustness/t14_artifact_manifest.json", 12374, "de9f59afe271f7e39b4b19fd10f9c3bd94c96023864a10a1c07080a12748a152"],
  ["O13_BOOTSTRAP_INDICES", "/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p13_20261027_r01/robustness/bootstrap_indices.json", 621, "69b04a05ab84bda11f2fc377f1f6197fae2609bf4808f14c53666f7351789927"],
  ["O13_CANDIDATE_SCORES", "/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p13_20261027_r01/robustness/candidate_scores.csv", 27898, "2328f691535ffb1ad6d9c0245c2b70b7b41e1b30ab4330d0495b8c2f7966aa9f"],
  ["O13_ROBUSTNESS_REPORT", "/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p13_20261027_r01/robustness/robustness_report.json", 6949, "d8b7782d262201564464b133b1f803e919284bdea823be4c797556013f5cc6d5"],
  ["P12_CANDIDATES", "/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p12_20261026/candidates.csv", 15344, "5721d619d93811a96e3b623f63145f4af060de1ff525d07fdd672c2d2fea6042"],
  ["P12_OUTPUTS_MANIFEST", "/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p12_20261026/outputs_manifest.json", 1009, "291803b0447407a5ffcf8ee43ac6a88e36eef7f746ccde988024185d27ee1753"],
  ["P3_DATA_OUTPUTS_MANIFEST", "/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p3_20261015/data/outputs_manifest.json", 1587, "fe4e561faf809597ab997ce7f2eedb9e6deeb2c4bf2c71f2861c8d8160a88771"],
  ["P3_STRUCTURES", "/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p3_20261015/data/structures.csv", 495033, "426d844b4854c95593e3e9b621637d12f42eb4422f6ad75b8e6e67a86d837000"],
  ["R05_RESUME_DECISION", "/Users/jingbozuo/Projects/aln-data-backend-ml/backend/surrogate_data/optimization_p13_20261027_r01/resume_20261029_r01/signoff/resume_decision.json", 2997, "391b2f5ceab034aeba08573957c59da3a66d0fd4bd2f859e79c0bb1d82525408"],
] as const;
