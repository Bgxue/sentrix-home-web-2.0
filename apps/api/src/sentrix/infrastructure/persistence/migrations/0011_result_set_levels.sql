-- 结果集的每条候选带上融合层的证据分级。
--
-- 1.0 的检索预览给模型一个 ``level``（``exact`` / ``strong`` / ``approximate``，
-- ``agent_runtime/tools.py:564`` 与 ``:1770``），模型据以判断「这条命中的是确定性
-- 锚点还是相似度」。2.0 的 ``fuse()`` 一直在算 ``FusedCandidate.evidence_class``，
-- 但 ``RetrieveEvidence`` 在转成 ``EvidenceHit`` 时丢掉了它——信息到不了预览。
--
-- 存在结果集这一侧而不是每次重算：结果集是**一次检索的冻结快照**，
-- ``get_result_page`` 读的就是库里这一份；重算会让同一张图在两页上有不同的分级。
ALTER TABLE agent_result_sets ADD COLUMN evidence_classes_json TEXT NOT NULL DEFAULT '{}';
