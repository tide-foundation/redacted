"""Keep CPU checkpoint tensors file-backed in the pinned OPF dependency.

Upstream copies every safetensors tensor into anonymous RAM. Reuse the mapped
storage on CPU instead, allowing the OS to reclaim clean weights under pressure.
No weights, precision, expert selection or inference operations are changed.
Fail the build if an OPF update changes the expected loading code.
"""
from importlib.util import find_spec
from pathlib import Path


def patch(source):
    original = '            param.data.copy_(loaded_tensor)'
    replacement = '''            if device.type == "cpu":
                # Redacted: retain reclaimable safetensors-backed CPU storage.
                param.data = loaded_tensor.to(dtype=param.dtype)
            else:
                param.data.copy_(loaded_tensor)'''
    batch = '            effective_batch = self.torch_ops_batch'
    bounded = '            effective_batch = 4 if t.device.type == "cpu" else self.torch_ops_batch'
    if replacement in source and bounded in source:
        return source
    if source.count(original) != 1 or source.count(batch) != 1:
        raise RuntimeError('OPF checkpoint loader changed; review the CPU memory patch.')
    return source.replace(original, replacement).replace(batch, bounded)


if __name__ == '__main__':
    path = Path(find_spec('opf').origin).parent / '_model' / 'model.py'
    path.write_text(patch(path.read_text()))
