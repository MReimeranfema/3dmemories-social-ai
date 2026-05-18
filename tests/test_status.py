# TODO: Tests für State Machine
# Sobald is_transition_allowed() implementiert ist:
#
# def test_allowed_transitions():
#     assert is_transition_allowed(JobStatus.PENDING, JobStatus.IN_PROGRESS)
#     assert is_transition_allowed(JobStatus.IN_PROGRESS, JobStatus.COMPLETED)
#     assert is_transition_allowed(JobStatus.FAILED, JobStatus.PENDING)
#
# def test_forbidden_transitions():
#     assert not is_transition_allowed(JobStatus.COMPLETED, JobStatus.IN_PROGRESS)
#     assert not is_transition_allowed(JobStatus.ARCHIVED, JobStatus.PENDING)
