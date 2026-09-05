package invoke

// The registered operation table: tool names as backends report them → the
// class the broker enforces. Registered here, never adapter self-assertion.
// Anything unknown is irreversible: the safe direction for a broker.
var operationTable = map[string]OperationClass{
	// claude CLI tools
	"Read": OpQuery, "Glob": OpQuery, "Grep": OpQuery, "LS": OpQuery, "TodoRead": OpQuery,
	"WebFetch": OpQuery, "WebSearch": OpQuery, // network reads; denied by the default ToolPolicy, an operator may allow them
	"Write": OpWriteLocal, "Edit": OpWriteLocal, "MultiEdit": OpWriteLocal, "NotebookEdit": OpWriteLocal, "TodoWrite": OpWriteLocal,
	"Bash": OpIrreversible, // a shell can do anything; unclassifiable ⇒ irreversible
	"Task": OpIrreversible, "Agent": OpIrreversible,
}

// ClassOf returns the registered class for a tool name; unknown ⇒ irreversible.
func ClassOf(op string) OperationClass {
	if c, ok := operationTable[op]; ok {
		return c
	}
	return OpIrreversible
}
