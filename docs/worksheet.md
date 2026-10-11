## Run it on your own desk

Pick one agentic workflow you run now, or one you plan to build. Fill in each line below on paper before you write any code. If a line stays empty, write down why.

1. **Domain.** One sentence: what goes in, and what comes out.

   >

2. **The six properties.** Mark each one yes or no. If you mark fewer than three yes, stop here and write it as one agent.

   - Runtime graph:
   - Unbounded step:
   - No undo:
   - Nondeterminism:
   - State outlives compute:
   - A person is inside:

3. **The stage graph.** Name each stage in order, and mark the stages that need a real agent loop. The rest get an empty grant and one call.

   >

4. **Each phase's grant.** List the function ids each phase may call. Include `engine::functions::list` and `engine::functions::info`. Mark every function that no stock worker registers, because you write those.

   >

5. **The rules.** For each rule, write its rung (0 to 5) and its policy (`refuse`, `ask`, or `warn`). If a rule needs a count across calls, move it into a function.

   >

6. **The boundary that holds.** Name the one mechanism that stops the worst outcome if every rule has a bug.

   >

7. **What a person must answer.** List each decision, and pick the oversight level: `manual`, `attended`, `supervised`, or `dark`.

   >

8. **What the record must hold.** List the entries someone will need in six months to answer "who allowed this?"

   >

9. **The tier of each piece.** Write a tier next to every item in lines 3 to 8. Use the reach order: 1 stock or registry worker, 2 config, 3 script, prompt, or predicate, 4 your own worker. For each 4, write one sentence on why tiers 1 to 3 were not enough.

   >

Then take the card from chapter 1, and for each line above, find the row that already answers it. Count the rows you have and the rows you must build.

- Rows you have:
- Rows you must build:

Last, test the sheet with three questions, each answered in this chapter or the one it names.

- **Line 5.** For each rule at rung 3, which arguments of one call does it read?

  >

- **Line 6.** Does that mechanism still hold if the ladder worker is down? If it is a rule, it holds only because the factory checks for `ladder::gate` first, as chapter 8 does.

  >

- **Line 4.** Can a turn in any phase do something that leaves the machine? If it can, move that action into a stage, as chapter 8 did with commit and push.

  >
