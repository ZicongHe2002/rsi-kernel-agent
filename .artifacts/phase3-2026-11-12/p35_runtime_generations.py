"""Pure Runtime generation parsing shared by repeated recovery helpers."""
import re
from rsi_kernel_agent.profiler.outputs import require


def generation_number(value, *, minimum=1):
    require(type(value) is str and re.fullmatch(r'runtime[1-9][0-9]*', value),
            'Invalid Runtime generation')
    number = int(value[7:])
    require(number >= minimum, 'Runtime generation is below the supported minimum')
    return number


def previous_generation(value, *, minimum=2):
    number = generation_number(value, minimum=minimum)
    require(number > minimum, 'Runtime generation has no previous replacement generation')
    return 'runtime' + str(number - 1)
