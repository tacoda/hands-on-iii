# Document the ports

## What

`README.md` never says which port anything listens on. Add a short section to
`README.md` that names each port the repository declares, and where it is
declared, read from the files rather than guessed. If the repository declares
no port, the section says so in one sentence.

## Why

The first thing anyone does after starting something is try to reach it, and
today that means reading the configuration to find out where.

## Acceptance criteria

- `README.md` gains one section about ports.
- Every port it names appears in a file in the repository. Do not invent one.
- Nothing outside `README.md` changes.

## Out of scope

Do not change any configuration file. Do not restructure the rest of the README.
