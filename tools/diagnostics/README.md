# Sanitized serial diagnostics

The parsers whitelist numeric connection/readiness/transport fields and a limited
application ELF prefix; raw lines, chat, audio and credentials are not saved.
`capture_connecting_state.ps1` is the preserved bounded 45-second Windows example
using COM7. Review the port before an explicitly approved run. It opens exclusively,
does not take over an occupied port or write commands, and closes in finally.
Opening the serial port may reset the attached device even with DTR/RTS requested
false. No capture was run while publishing this checkpoint.

Connection readiness does not establish LCD text or voice workflow completion.
Filter timestamps are host ingestion times and can lag due to buffering. Output
files are private observations and should not be committed as source.
