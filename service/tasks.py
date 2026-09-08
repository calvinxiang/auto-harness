"""Fixed evaluation subset; no user-controlled datasets or executable task paths."""
HOLDOUT_TASKS = ['cancel-async-tasks', 'openssl-selfsigned-cert', 'large-scale-text-editing']

TASKS = {
    'fix-git': 'Git recovery and repository inspection',
    'regex-log': 'Text processing and precise output formatting',
    'git-leak-recovery': 'Repository maintenance and secret removal',
    'log-summary-date-ranges': 'Log parsing and date arithmetic',
    'build-cython-ext': 'Build configuration and Python extension tooling',
    'configure-git-webserver': 'Service configuration and Git',
    'fix-code-vulnerability': 'Debugging and secure code repair',
    'extract-elf': 'Binary inspection and data extraction',
    'nginx-request-logging': 'Web server configuration and log verification',
    'sqlite-db-truncate': 'Database file recovery and debugging',
}
