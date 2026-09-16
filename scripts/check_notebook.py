"""Validate and execute the demo without committing generated cell outputs."""
import argparse
import os
from pathlib import Path

import nbformat
from nbclient import NotebookClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--in-process', action='store_true',
                        help='Execute cells with IPython when the runtime disallows local kernel sockets')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    notebook = nbformat.read(root / 'notebooks/reconciliation_demo.ipynb', as_version=4)
    nbformat.validate(notebook)
    if args.in_process:
        from IPython.core.interactiveshell import InteractiveShell
        from IPython.utils.capture import capture_output
        os.chdir(root)
        shell = InteractiveShell()
        for index, cell in enumerate(notebook.cells):
            if cell.cell_type == 'code':
                with capture_output():
                    result = shell.run_cell(cell.source)
                if not result.success:
                    raise RuntimeError(f'Notebook cell {index} failed') from (result.error_before_exec or result.error_in_exec)
        print('Notebook cells executed successfully in process; source remains output-free.')
    else:
        NotebookClient(notebook, timeout=180, kernel_name='python3',
                       resources={'metadata': {'path': str(root)}}).execute()
        print('Notebook executed successfully in a fresh kernel; source remains output-free.')


if __name__ == '__main__':
    main()
