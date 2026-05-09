# SDK Reference

## Python

### Install

```bash
pip install "akernl[serve]"
```

### Client initialization

```python
from akernl.sdk import AkernlClient

client = AkernlClient(
    base_url="http://localhost:8080",
    api_key="sk-akernl-...",
    timeout=30,
)
```

### Create sandbox and execute

```python
with client.sandbox(language="python") as sb:
    result = sb.execute("python", "print(2 + 2)")
    print(result.stdout)   # 4
    print(result.status)   # success
```

`client.sandbox()` creates the sandbox on enter and deletes it on exit.

### Execute more code (state retained)

```python
with client.sandbox() as sb:
    sb.execute("python", "x = 10")
    result = sb.execute("python", "print(x * 3)")
    print(result.stdout)   # 30
```

### File operations

```python
with client.sandbox() as sb:
    # upload
    sb.upload("/workspace/data.csv", b"a,b\n1,2\n3,4\n")

    # download
    file = sb.download("/workspace/data.csv")
    print(file.content)    # b"a,b\n1,2\n3,4\n"

    # list
    listing = sb.list_files("/workspace")
    for f in listing.files:
        print(f.name, f.type, f.size)

    # delete
    sb.delete_file("/workspace/data.csv")
```

### Install dependencies

```python
with client.sandbox(security_mode="standard") as sb:
    result = sb.install("python", ["pandas", "numpy==1.26.0"])
    print(result.installed)   # [{"name": "pandas", "version": "2.2.1"}, ...]
    print(result.failed)      # []
```

### Run shell command

```python
with client.sandbox() as sb:
    result = sb.command("ls /workspace")
    print(result.stdout)
```

### Get sandbox info

```python
sb = client.create_sandbox()
info = client.get_sandbox(sb.id)
print(info.state, info.execution_count, info.expires_at)
```

### Delete sandbox

```python
sb = client.create_sandbox()
client.delete_sandbox(sb.id)
```

### Error handling

```python
from akernl.sdk import AkernlClient, SandboxError, TimeoutError, PoolExhaustedError

try:
    with client.sandbox() as sb:
        result = sb.execute("python", "import time; time.sleep(999)", timeout_ms=1000)
except TimeoutError:
    print("execution timed out")
except PoolExhaustedError:
    print("no VMs available, retry later")
except SandboxError as e:
    print(f"error {e.code}: {e.message}")
```

---

## TypeScript

### Install

```bash
npm install @cottus-ai/akernl
```

### Client initialization

```typescript
import { AkernlClient } from "@cottus-ai/akernl";

const client = new AkernlClient({
  baseUrl: "http://localhost:8080",
  apiKey: "sk-akernl-...",
});
```

### Create sandbox and execute

```typescript
const sb = await client.createSandbox({
  initialCode: "console.log(2 + 2)",
  initialLanguage: "nodejs",
});
console.log(sb.initialResult?.stdout);  // 4
await client.deleteSandbox(sb.id);
```

### Execute more code (state retained)

```typescript
const sb = await client.createSandbox();
await client.execute(sb.id, { language: "nodejs", code: "let x = 10;" });
const result = await client.execute(sb.id, { language: "nodejs", code: "console.log(x * 3);" });
console.log(result.stdout);  // 30
await client.deleteSandbox(sb.id);
```

### File operations

```typescript
// upload
await client.uploadFile(sb.id, {
  path: "/workspace/data.json",
  content: Buffer.from('{"key": "value"}'),
});

// download
const file = await client.downloadFile(sb.id, "/workspace/data.json");
console.log(file.content.toString());

// list
const listing = await client.listFiles(sb.id, "/workspace");
listing.files.forEach(f => console.log(f.name, f.type));

// delete
await client.deleteFile(sb.id, "/workspace/data.json");
```

### Install dependencies

```typescript
const result = await client.installPackages(sb.id, {
  language: "nodejs",
  packages: [{ name: "lodash" }, { name: "axios", version: "1.6.0" }],
});
console.log(result.installed);
```

### Run shell command

```typescript
const result = await client.runCommand(sb.id, { command: "ls /workspace" });
console.log(result.stdout);
```

### Get sandbox info

```typescript
const info = await client.getSandbox(sb.id);
console.log(info.state, info.executionCount, info.expiresAt);
```

### Error handling

```typescript
import { AkernlError, TimeoutError, PoolExhaustedError } from "@cottus-ai/akernl";

try {
  await client.execute(sb.id, { language: "nodejs", code: "...", timeoutMs: 1000 });
} catch (e) {
  if (e instanceof TimeoutError) console.error("timed out");
  else if (e instanceof PoolExhaustedError) console.error("retry later");
  else if (e instanceof AkernlError) console.error(e.code, e.message);
}
```

---

## Go

### Install

```bash
go get go.cottus.ai/akernl
```

### Client initialization

```go
import "go.cottus.ai/akernl"

client := akernl.NewClient(akernl.Config{
    BaseURL: "http://localhost:8080",
    APIKey:  "sk-akernl-...",
})
```

### Create sandbox and execute

```go
sb, err := client.CreateSandbox(ctx, akernl.CreateSandboxRequest{
    InitialCode:     "fmt.Println(2 + 2)",
    InitialLanguage: "go",
})
if err != nil {
    log.Fatal(err)
}
fmt.Println(sb.InitialResult.Stdout)  // 4
defer client.DeleteSandbox(ctx, sb.ID)
```

### Execute more code

```go
_, err = client.Execute(ctx, sb.ID, akernl.ExecuteRequest{
    Language: "python",
    Code:     "x = 10",
})

result, err := client.Execute(ctx, sb.ID, akernl.ExecuteRequest{
    Language: "python",
    Code:     "print(x * 3)",
})
fmt.Println(result.Stdout)  // 30
```

### File operations

```go
// upload
err = client.UploadFile(ctx, sb.ID, akernl.UploadRequest{
    Path:    "/workspace/data.json",
    Content: []byte(`{"key": "value"}`),
})

// download
file, err := client.DownloadFile(ctx, sb.ID, "/workspace/data.json")
fmt.Println(string(file.Content))

// list
listing, err := client.ListFiles(ctx, sb.ID, "/workspace")
for _, f := range listing.Files {
    fmt.Println(f.Name, f.Type)
}

// delete
err = client.DeleteFile(ctx, sb.ID, "/workspace/data.json")
```

### Install dependencies

```go
result, err := client.InstallPackages(ctx, sb.ID, akernl.InstallRequest{
    Language: "python",
    Packages: []akernl.Package{
        {Name: "pandas"},
        {Name: "numpy", Version: "1.26.0"},
    },
})
fmt.Println(result.Installed)
```

### Run shell command

```go
result, err := client.RunCommand(ctx, sb.ID, akernl.CommandRequest{
    Command: "ls /workspace",
})
fmt.Println(result.Stdout)
```

### Get sandbox info

```go
info, err := client.GetSandbox(ctx, sb.ID)
fmt.Println(info.State, info.ExecutionCount, info.ExpiresAt)
```

### Error handling

```go
import "errors"

result, err := client.Execute(ctx, sb.ID, akernl.ExecuteRequest{...})
if err != nil {
    var akErr *akernl.Error
    if errors.As(err, &akErr) {
        switch akErr.Code {
        case akernl.ErrTimeout:
            // execution timed out
        case akernl.ErrPoolExhausted:
            // retry with back-off
        default:
            log.Printf("akernl error %s: %s", akErr.Code, akErr.Message)
        }
    }
}
```
