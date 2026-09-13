<?php
// Rootless fallback matching the small PDO surface used by feedback_service.php.
final class FeedbackPythonDatabase {
    private $process;
    private array $pipes = [];
    public function __construct(string $path) {
        $this->process = proc_open(['/usr/bin/python3', __DIR__ . '/feedback_store.py', $path],
            [0 => ['pipe', 'r'], 1 => ['pipe', 'w'], 2 => ['file', '/dev/null', 'a']], $this->pipes);
        if(!is_resource($this->process)) throw new RuntimeException('Feedback storage unavailable');
        stream_set_timeout($this->pipes[1], 7);
    }
    public function run(string $sql, array $params = []): array {
        $request = json_encode(['sql' => $sql, 'params' => $params], JSON_THROW_ON_ERROR) . "\n";
        if(strlen($request) > 32768 || fwrite($this->pipes[0], $request) !== strlen($request))
            throw new RuntimeException('Feedback storage write failed');
        $line = fgets($this->pipes[1], 65538);
        if($line === false || !str_ends_with($line, "\n")) {
            proc_terminate($this->process);
            throw new RuntimeException('Feedback storage timed out');
        }
        $result = json_decode($line, true, 512, JSON_THROW_ON_ERROR);
        if(($result['ok'] ?? false) !== true) throw new RuntimeException('Feedback storage query failed');
        return $result['rows'];
    }
    public function exec(string $sql): void { $this->run($sql); }
    public function prepare(string $sql): FeedbackPythonStatement { return new FeedbackPythonStatement($this, $sql); }
    public function query(string $sql): FeedbackPythonStatement {
        $statement = $this->prepare($sql); $statement->execute(); return $statement;
    }
    public function __destruct() {
        foreach($this->pipes as $pipe) if(is_resource($pipe)) fclose($pipe);
        if(is_resource($this->process)) { proc_terminate($this->process); proc_close($this->process); }
    }
}
final class FeedbackPythonStatement {
    private FeedbackPythonDatabase $database;
    private string $sql;
    private array $rows = [];
    public function __construct(FeedbackPythonDatabase $database, string $sql) {
        $this->database = $database; $this->sql = $sql;
    }
    public function execute(array $params = []): void { $this->rows = $this->database->run($this->sql, $params); }
    public function fetch($mode = null) { return array_shift($this->rows) ?? false; }
    public function fetchColumn() {
        $row = $this->fetch(); return $row === false ? false : array_values($row)[0];
    }
}
