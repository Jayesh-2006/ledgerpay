# LedgerPay interview guide

## 30-second overview

I built LedgerPay, a simulated INR wallet API with FastAPI and PostgreSQL. It supports wallet creation, funding, transfers, and transaction history. Transfers lock both wallet rows and commit the debit, credit, and records together. Funding and transfer retries use a PostgreSQL-backed idempotency key so the same request moves money once. I tested concurrent spending, retries, and rollback against a real PostgreSQL database.

## 2-minute explanation

A client creates two wallets and adds simulated funds to one of them. I represent money as integer paise, so ₹25.50 is 2550. FastAPI routes requests and Pydantic rejects invalid inputs such as fractional paise and identical sender and receiver IDs. Synchronous SQLAlchemy sessions connect the routes to PostgreSQL.

The most important case is two requests spending from the same wallet. Suppose it holds ₹1,000 and both requests try to transfer ₹800. Each tries to lock the relevant wallet rows in ascending ID order. One request locks the sender first, reads the balance, and commits the debit and credit. The other waits. When it acquires the lock, it sees ₹200 and returns a conflict for insufficient funds. If inserting the history rows fails, the balances and transfer roll back together.

I also handled clients retrying after a timeout. The payment transaction first reserves a key scoped to a demo client and operation. PostgreSQL's uniqueness constraint makes simultaneous duplicates wait. If the first transaction commits, the retry returns the saved response for the same payload, or a conflict if the payload changed. If the transaction rolls back, the key becomes available again. I verified both concurrency cases using real PostgreSQL lock waits, and ran the API through a Postman collection that demonstrates the results.

## Detailed technical explanation

Start in [app/main.py](../app/main.py). Follow a POST through the [wallet](../app/api/wallets.py) or [transfer](../app/api/transfers.py) route, then through [Pydantic schemas](../app/schemas.py) and the [payment functions](../app/services/payments.py).

Explain the two kinds of coordination separately.

1. The unique key in [the idempotency service](../app/services/idempotency.py) coordinates attempts at the same operation. It protects a client that retries one request.
2. `SELECT ... ORDER BY id FOR UPDATE` coordinates any operation writing the same wallets, even when the keys differ. It protects the balance when separate payments compete.

Point to `with session.begin()` and describe the failure boundary. A flush issues SQL and obtains generated IDs. The context manager commits on success. If inserting history fails, PostgreSQL discards all writes made since the transaction began, including the key reservation. At Read Committed, a conflicting retry uses a separate SELECT after the insert attempt, so it can see the row the other transaction just committed. Walk through [the competing transfers example](architecture.md#two-payments-competing-for-the-same-balance) and point to [the tests](../tests).

## Interview questions

1. **Why FastAPI?** It lets me use typed route functions with Pydantic validation and generates OpenAPI and Swagger UI from those definitions.
2. **Why PostgreSQL?** I needed persistent relational data, constraints, transactions, and row locks I could exercise with concurrent connections.
3. **What makes this a REST API?** Paths identify resources such as wallets and transfers. HTTP methods describe reads and writes, and responses use JSON and meaningful status codes.
4. **What happens when an HTTP request arrives?** Uvicorn passes it to FastAPI. The router finds a method and path match, validates input, supplies the request's session, calls the function, and serializes its response.
5. **What is SQLAlchemy?** It maps Python classes to PostgreSQL tables and builds parameterized statements for operations such as selecting wallets and inserting a transfer.
6. **What is a database session?** It is the request's unit of interaction with the database. It tracks ORM changes, flushes them as SQL, and coordinates transaction boundaries.
7. **What is ACID?** Atomicity groups writes, consistency preserves invariants, isolation coordinates concurrent work, and durability preserves committed data.
8. **What happens in a transfer?** Reserve the request key, lock both wallets, check balances, apply debit and credit, record the transfer and both history rows, save the response, and commit.
9. **Why is rollback important?** A failure writing a history entry must undo the debit and credit too. It also releases the key so a retry can try again.
10. **What is a race condition here?** Two requests could both read the same available balance before either updates it and each decide to spend it.
11. **Why use `SELECT FOR UPDATE`?** The balance is read while the row is locked. A competing writer must wait until the transaction that made the first decision finishes.
12. **How does this prevent a lost update?** The waiting writer reads the updated row once it acquires the lock and computes the change from that newer balance.
13. **Why acquire locks in ID order?** Opposite-direction transfers request locks in the same order, avoiding a cycle where each holds the other's first row.
14. **What is idempotency in LedgerPay?** Repeating the same successful payment with the same scope, key, and validated payload returns the original response with one balance change.
15. **Why do payment systems need it?** If the client loses the response, it cannot tell whether the payment committed. A stable key lets the client retry safely.
16. **What happens to concurrent duplicates?** PostgreSQL makes the second insert wait for the first transaction. After commit the duplicate replays or conflicts. After rollback it can reserve the key itself.
17. **What does the request hash cover?** It covers the validated amount and wallet IDs. Funding includes its path ID. Sorted JSON keys make field order irrelevant.
18. **Why store money as integer paise?** Integers represent the smallest supported unit exactly. PostgreSQL constraints and an API cap prevent invalid balances and overflow.
19. **How are requests validated?** Pydantic checks names, required fields, strict integers and bounds. The header dependency validates key format. The service checks rules that depend on current balances.
20. **Why keep both a balance and history?** The balance provides a quick current value. History shows how it changed. They are updated inside the same transaction.
21. **How do you test rollback?** A test-only constraint rejects transfer history after the transfer has begun. The test verifies balances, history, transfer rows, and the key, then retries successfully.
22. **How do you test concurrency?** Two TestClient requests use separate PostgreSQL sessions. The test holds a wallet lock and observes both requests waiting before letting them proceed.
23. **What happens after a lock timeout or lost response?** The client retries with the same key and payload. It can recover a committed response or execute if the original transaction rolled back.
24. **What are the limitations?** This is a local INR simulation. The caller-chosen client ID is unauthenticated, history is an activity log, and successful keys have no retention policy.
25. **What would you improve next?** For a multi-user deployment I would authenticate callers, verify wallet ownership, and derive the key scope from the authenticated account. For ongoing schema changes I would add versioned migrations.

## Resume bullets

- Built an INR wallet REST API with Python, FastAPI, Pydantic, SQLAlchemy, and PostgreSQL for simulated funding, transfers, balances, and paginated transaction history.
- Implemented atomic wallet transfers with ordered PostgreSQL row locks and database constraints. Verified concurrent overspend protection, balance conservation, and rollback after an injected write failure.
- Designed PostgreSQL-backed idempotency for safe funding and transfer retries. Tested response replay, conflicting payloads, and concurrent duplicate requests with pytest and FastAPI TestClient.
