# Contributing Guide

Guidelines for contributing to the 5G KubeEdge Testbed project.

## Getting Started

1. Fork the repository
2. Clone your fork
3. Create a feature branch
4. Make changes
5. Run tests
6. Submit a pull request

## Project Structure

```
kelt/
├── ansible/
│   ├── phases/              # Deployment phases (01-06)
│   │   ├── 01-infrastructure/
│   │   ├── 02-kubernetes/
│   │   ├── 03-kubeedge/
│   │   ├── 04-overlay-network/
│   │   ├── 05-5g-core/
│   │   └── 06-ueransim-mec/
│   ├── group_vars/          # Global variables
│   ├── inventory.ini        # Host inventory
│   └── ansible.cfg          # Ansible configuration
├── docs/                    # Documentation
├── tests/                   # Test suites
├── Vagrantfile              # VM definitions
├── LICENSE                  # Apache 2.0
└── README.md
```

## Coding Standards

### Ansible

- Use YAML syntax consistently
- Include comments for complex tasks
- Use variables from `group_vars/all.yml`
- Follow role structure: `tasks/`, `templates/`, `handlers/`, `defaults/`

```yaml
# Good
- name: Install required packages
  ansible.builtin.apt:
    name: "{{ item }}"
    state: present
  loop: "{{ required_packages }}"

# Bad
- apt: name=curl state=present
```

### Python (Tests)

- Follow PEP 8
- Use type hints
- Include docstrings
- Handle exceptions properly

```python
def test_feature(self) -> bool:
    """Test feature description."""
    try:
        result = self.do_something()
        return result is not None
    except Exception as e:
        self.logger.error(f"Test failed: {e}")
        return False
```

### Documentation

- Use clear, concise English
- Include code examples
- Add diagrams for complex concepts
- Keep formatting consistent

## Development Workflow

### 1. Setup Development Environment

```bash
# Clone
git clone https://github.com/your-fork/kelt.git
cd kelt

# Start VMs
vagrant up

# Verify
vagrant ssh master -c "sudo k3s kubectl get nodes"
```

### 2. Make Changes

```bash
# Create branch
git checkout -b feature/my-feature

# Edit files
vim ansible/phases/05-5g-core/roles/nf_deployments/tasks/main.yml

# Test changes
vagrant ssh ansible
cd ~/ansible-ro
ansible-playbook phases/05-5g-core/playbook.yml -i inventory.ini
```

### 3. Run Tests

```bash
cd tests
make e2e
```

### 4. Commit Changes

```bash
git add .
git commit -m "feat: add new feature

- Description of change 1
- Description of change 2"
```

### 5. Submit Pull Request

- Describe what changed and why
- Reference related issues
- Include test results

## Commit Messages

Follow conventional commits:

```
type(scope): description

[optional body]

[optional footer]
```

Types:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation
- `refactor`: Code refactoring
- `test`: Test changes
- `chore`: Maintenance

Examples:
```
feat(5g-core): add BSF network function
fix(ueransim): correct IMSI generation
docs(architecture): update network topology diagram
```

## Git hooks (developer-only)

Optional local hooks guard the commit/push workflow. They are opt-in and not
part of the operator install:

```
testbed dev-hooks on       # install (sets core.hooksPath=.githooks)
testbed dev-hooks status   # show hook + release state
testbed dev-hooks off      # uninstall
```

- **pre-commit** runs `gitleaks` on staged changes and blocks the commit if it
  finds a secret (mirrors the CI scan). Install `gitleaks` locally for this to
  apply; without it the scan is skipped and CI still scans on push.
- **pre-push** is advisory only (never blocks): it reminds you when the frontend
  has changes that need a version tag to publish, and flags WIP commits.

## Publishing images (what needs a tag)

Each image has its own release lifecycle, decoupled from the others:

- **Dashboard frontend** publishes only on a `dashboard-frontend-v<semver>` git
  tag. Editing `dashboard/frontend/` does not change the published `:latest`
  until you tag. Use the confirmed helper:
  ```
  testbed dev-hooks release          # tags the current package.json version, or
                                     # offers a patch/minor/major bump, then pushes
  ```
- **Docs** (`kelt-docs`) publish **automatically**: the `docs-site` workflow
  rebuilds the image on any push touching `docs/**`, so no manual tag is needed.
  The in-cluster docs pod runs `:latest`; re-running phase 09 forces a rollout so
  it pulls the freshly built image.
- **NF / northbound images** live in their own repositories (`5g-nf-platform`,
  `5g-northbound`) and are tagged there, not from this repo.

## Adding a New Phase

1. Create phase directory:
```
ansible/phases/07-new-feature/
├── playbook.yml
├── README.md
└── roles/
    └── new_role/
        ├── tasks/main.yml
        ├── templates/
        ├── handlers/main.yml
        └── defaults/main.yml
```

2. Add to main playbook (`phases/00-main-playbook.yml`):
```yaml
- import_playbook: phases/07-new-feature/playbook.yml
  tags: [phase7, new-feature]
```

3. Document in `docs/deployment/phases.md`

4. Add tests in `tests/`

## Adding Documentation

1. Create file in appropriate directory:
   - `docs/architecture/` - System design
   - `docs/deployment/` - Setup guides
   - `docs/operations/` - Operational procedures
   - `docs/development/` - Developer guides
   - `docs/runbooks/` - Diagnostic procedures
   - `docs/known-issues/` - Platform limitations

2. Update `docs/README.md` index

3. Use consistent formatting:
   - H1 for title
   - H2 for major sections
   - Code blocks with language hints
   - Tables for structured data

## Testing Guidelines

### Write Tests For

- New features
- Bug fixes
- Configuration changes

### Test Categories

- **E2E**: Full system validation
- **Unit**: Individual components
- **Integration**: Component interactions

### Running Tests

```bash
cd tests

# All tests
make

# Specific suite
make e2e

# Verbose
python3 run_tests.py -s e2e -v
```

## Code Review Checklist

- [ ] Code follows project style
- [ ] Tests pass
- [ ] Documentation updated
- [ ] No hardcoded values (use variables)
- [ ] Error handling present
- [ ] Commit messages follow convention

## Getting Help

- Check existing documentation
- Search closed issues
- Open a new issue with:
  - Clear description
  - Steps to reproduce
  - Expected vs actual behavior
  - Environment details

## License

By contributing, you agree that your contributions will be licensed under the Apache 2.0 License.
