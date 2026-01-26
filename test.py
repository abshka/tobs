def divide_nums(num1, num2):
    result = 0
    try:
        result = float(num1) / float(num2)
    except ZeroDivisionError:
        print("На ноль делить нельзя.")
    except ValueError:
        print("Функция деления работает только с числами.")
    except Exception as e:
        print(e)
    finally:
        return result
