import sys

# Useful for viewing the first few lines of a file if it is too big to be opened with a text editor.
# Usage: python peek.py <filename> <num_lines>

def peek_file(filename, num_lines):
    try:
        with open(filename, 'r') as file:
            for i in range(num_lines):
                line = file.readline()
                if not line:
                    break
                print(line, end='')
    except FileNotFoundError:
        print(f"Error: File '{filename}' not found.")
    except ValueError:
        print("Error: Please provide a valid number of lines.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python peek.py <filename> <num_lines>")
    else:
        filename = sys.argv[1]
        try:
            num_lines = int(sys.argv[2])
            peek_file(filename, num_lines)
        except ValueError:
            print("Error: num_lines must be an integer.")
