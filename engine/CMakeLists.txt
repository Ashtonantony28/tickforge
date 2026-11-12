add_executable(tf_tests
  tests/test_main.cpp
  tests/test_flat_map.cpp
  tests/test_itch.cpp
  tests/test_order_book.cpp
  tests/test_fixture.cpp)
target_link_libraries(tf_tests PRIVATE tickforge_engine)
target_compile_definitions(tf_tests PRIVATE TF_FIXTURE_DIR="${PROJECT_SOURCE_DIR}/fixtures")
add_test(NAME engine_unit COMMAND tf_tests)

add_executable(tf_replay tools/tf_replay.cpp)
target_link_libraries(tf_replay PRIVATE tickforge_engine)

add_executable(tf_bench bench/tf_bench.cpp)
target_link_libraries(tf_bench PRIVATE tickforge_engine)
