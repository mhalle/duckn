# Questionnaire: what a reader recovers from one header

Answer from the header and the specification ONLY. For every answer, cite the field(s) and the
spec section you used. When the header does not state something, answer "not stated" (never
guess a default). When the header or the spec is ambiguous, say so and give each reading.

Q1. The world: how many axes; for each, what it measures (space, time, other), its unit, and
    which way its coordinates increase (anatomically, if stated). The handedness, if derivable.
    The frame's identity, if stated.
Q2. Each array dimension, in order: does it move through the world (then: which world axes, the
    spacing, which way it runs anatomically), hold components (then: what they are), or state
    nothing?
Q3. The world position of: the first sample; the sample one index further along each
    dimension that moves through the world (one at a time); the last sample of the array.
Q4. The extent (bounding box) the array covers along each dimension that moves through the
    world, taking centering into account.
Q5. What the stored numbers mean: the quantity and unit, and the value a stored 1000 stands for
    (at index 0 of every dimension, if that matters).
Q6. If there are vector or tensor components: how to express them in the world's axes, and
    numerically, the world components of stored component vectors (1, 0, 0) and (0, 1, 0)
    (or of the stored tensor with only its first component 1).
Q7. If any dimension moves through time: the time of index 2 along it.
Q8. Which dimensions may be interpolated (resampled), and which may not.
Q9. For each transform in world.transforms: the first sample's coordinates in the target frame.
Q10. Everything you could not determine, anything ambiguous, and anything that looks like it
     breaks a rule of the spec.

Added for rounds 4-10:

Q11. Which statements in the file are about this array, and which are a record of its source?
     Where a fact appears in both (an identifier, a time), which one do you use, and why?
Q12. Can this array's positions, and its times if it has any, be compared directly with those of
     another array? With which arrays (say what they would have to carry)?
Q13. Which software wrote this file, and what processing preceded it? Could you tell a file from
     a writer later found defective?
Q14. For each value transform: what a stored 1000 means (at index 0 of every dimension, if that
     matters). If a transform cannot be applied, what can you still offer a caller, and is the
     file valid?
Q15. Does anything in the file contradict what a reader gets from the file alone? Which rule does
     it break, and what should the writer have done?
Q16. For a 1.x file: the 2.0 reading of it (the §14 mapping), and anything the mapping cannot
     carry over or must report.
