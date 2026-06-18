BEGIN;

UPDATE colony_mouse
SET strain_line_id = 60, updated_at = NOW()
WHERE strain_line_id IN (61, 62);

UPDATE colony_mousegenotypecomponent
SET strain_line_id = 60, updated_at = NOW()
WHERE strain_line_id IN (61, 62);

UPDATE colony_strainline
SET
  expected_loci_template = E'Gpr82flox\nlyz2 Cre\nSPP1flox',
  expected_loci_config = '[
    {"locus_name": "Gpr82flox", "locus_type": "floxed_allele", "chromosome_type": "autosomal"},
    {"locus_name": "lyz2 Cre", "locus_type": "cre_ki", "chromosome_type": "autosomal"},
    {"locus_name": "SPP1flox", "locus_type": "floxed_allele", "chromosome_type": "autosomal"}
  ]'::jsonb,
  notes = 'Merged Gpr82 flox het/hom lines on 2026-05-28.',
  updated_at = NOW()
WHERE id = 60;

UPDATE colony_strainline
SET
  is_active = false,
  notes = COALESCE(notes, '') || E'\nMerged into Gpr82 flox (id 60) on 2026-05-28.',
  updated_at = NOW()
WHERE id IN (61, 62);

COMMIT;

SELECT id, line_name, is_active,
       (SELECT COUNT(*) FROM colony_mouse m WHERE m.strain_line_id = sl.id) AS mice
FROM colony_strainline sl
WHERE id IN (60, 61, 62)
ORDER BY id;
