/* Execute the exact upstream byte-order header with side-effect inputs. */
#include <assert.h>
#include <endian.h>
#include <stdint.h>
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;
#include BYTEORDER_HEADER
int main(void) {
 u16 a=0x1234; u32 b=0x12345678; u64 c=UINT64_C(0x0123456789abcdef);
 assert(__hcxbswab16(a++)==0x3412); assert(a==0x1235);
 assert(__hcxbswab32(b++)==0x78563412); assert(b==0x12345679);
 assert(__hcxbswab64(c++)==UINT64_C(0xefcdab8967452301));
 assert(c==UINT64_C(0x0123456789abcdf0));
 return 0;
}
