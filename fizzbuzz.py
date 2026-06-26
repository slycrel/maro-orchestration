def fizzbuzz(n):
    """Return the FizzBuzz string for n.

    "Fizz" if divisible by 3, "Buzz" if divisible by 5,
    "FizzBuzz" if divisible by both, otherwise str(n).
    """
    if n % 15 == 0:
        return "FizzBuzz"
    if n % 3 == 0:
        return "Fizz"
    if n % 5 == 0:
        return "Buzz"
    return str(n)


if __name__ == "__main__":
    for i in range(1, 16):
        print(fizzbuzz(i))
