package workspace

import (
	"os"
	"path/filepath"
)

// WriteFileDurable writes bytes to a unique temp file beside path, fsyncs the
// file, renames it into place, then fsyncs the parent directory so the
// rename itself is durable across power loss. Any failure leaves either the
// old file or nothing — never a torn file at path.
func WriteFileDurable(path string, data []byte, perm os.FileMode) error {
	dir := filepath.Dir(path)
	tmp, err := os.CreateTemp(dir, "."+filepath.Base(path)+".*")
	if err != nil {
		return err
	}
	name := tmp.Name()
	fail := func(e error) error {
		tmp.Close()
		os.Remove(name)
		return e
	}
	if _, err := tmp.Write(data); err != nil {
		return fail(err)
	}
	if err := tmp.Chmod(perm); err != nil {
		return fail(err)
	}
	if err := tmp.Sync(); err != nil {
		return fail(err)
	}
	if err := tmp.Close(); err != nil {
		os.Remove(name)
		return err
	}
	if err := os.Rename(name, path); err != nil {
		os.Remove(name)
		return err
	}
	return SyncDir(dir)
}

// SyncDir fsyncs a directory so entries created or renamed into it are durable.
func SyncDir(dir string) error {
	d, err := os.Open(dir)
	if err != nil {
		return err
	}
	defer d.Close()
	return d.Sync()
}
